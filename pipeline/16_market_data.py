#!/usr/bin/env python3
"""Step 16 — Publish market price and market cap rows to D1.

This stage is intentionally self-contained in psxgpt-pipeline. It does not use
legacy psxgptinfra/psxgptinfrav3 market scripts.

The PSX DPS EOD payload shape is:
  [timestamp, close, volume, open]

Only price and market-cap fields are authoritative here. Derived valuation
fields such as P/E, P/B, dividend yield, and EV are cleared on refreshed rows;
they should be computed from market cap + sourced financial statement rows when
needed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import ARTIFACTS, PROJECT_ROOT  # noqa: E402
from shared.report import emit_decision, write_report  # noqa: E402

TICKERS_FILE = PROJECT_ROOT / "tickers100.json"
SQL_DIR = ARTIFACTS / "16_market_data"
D1_DB = "psx"
PSX_COMPANY_URL = "https://dps.psx.com.pk/company/{ticker}"
PSX_TIMESERIES_URL = "https://dps.psx.com.pk/timeseries/eod/{ticker}"


def http_get_text(
    url: str,
    user_agent: str = "Mozilla/5.0",
    timeout: int = 12,
    retries: int = 2,
    retry_sleep: float = 2.0,
) -> str | None:
    for attempt in range(retries + 1):
        result = subprocess.run(
            ["curl", "-fsSL", "--max-time", str(timeout), "-A", user_agent, url],
            text=True,
            capture_output=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        if attempt < retries:
            time.sleep(retry_sleep * (attempt + 1))
    return None


@dataclass
class MarketRow:
    ticker: str
    date: str
    close: float
    volume: int | None
    open: float | None
    total_shares: int
    market_cap: float
    stale_days: int
    status: str = "ok"


def load_tickers(ticker: str | None, tickers_csv: str | None) -> list[str]:
    if ticker:
        return [ticker.upper()]
    if tickers_csv:
        return [part.strip().upper() for part in tickers_csv.split(",") if part.strip()]
    data = json.loads(TICKERS_FILE.read_text())
    return [str(row["Symbol"]).upper() for row in data]


def parse_shares(html: str) -> int | None:
    patterns = [
        r'stats_label">\s*Shares\s*</div>\s*<div[^>]*class="stats_value"[^>]*>\s*([\d,]+)',
        r">Shares<[^>]*>[^<]*<[^>]*>([\d,]{3,})<",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def fetch_shares(ticker: str) -> int | None:
    html = http_get_text(
        PSX_COMPANY_URL.format(ticker=ticker),
        user_agent="Mozilla/5.0 (compatible; PSXBot/1.0)",
    )
    return parse_shares(html) if html else None


def fetch_latest_price(ticker: str) -> dict | None:
    try:
        text = http_get_text(PSX_TIMESERIES_URL.format(ticker=ticker))
        if not text:
            return None
        data = json.loads(text)
        rows = data.get("data") or []
        if data.get("status") != 1 or not rows:
            return None
        row = rows[0]
        if len(row) < 4:
            return None
        ts, close, volume, open_price = row[:4]
        close = float(close)
        date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        return {
            "date": date,
            "close": close,
            "volume": int(volume) if volume is not None else None,
            "open": float(open_price) if open_price is not None else None,
        }
    except Exception:
        return None


def collect_row(ticker: str, today: datetime) -> MarketRow | dict:
    price = fetch_latest_price(ticker)
    if not price or price["close"] <= 0:
        return {"ticker": ticker, "status": "no_price"}
    shares = fetch_shares(ticker)
    if not shares or shares <= 0:
        return {"ticker": ticker, "status": "no_shares", **price}
    date_obj = datetime.strptime(price["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    stale_days = (today - date_obj).days
    return MarketRow(
        ticker=ticker,
        date=price["date"],
        close=price["close"],
        volume=price["volume"],
        open=price["open"],
        total_shares=shares,
        market_cap=price["close"] * shares,
        stale_days=stale_days,
        status="stale" if stale_days > 30 else "ok",
    )


def sql_value(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def insert_sql(row: MarketRow) -> str:
    cols = [
        "ticker", "date", "closing_price", "total_shares", "market_cap",
        "pe_ratio", "pb_ratio", "dividend_yield", "enterprise_value",
        "fundamentals_period_end", "pe_earnings_basis",
        "pe_earnings_period_duration", "pe_earnings_section",
    ]
    values = [
        row.ticker, row.date, row.close, row.total_shares, row.market_cap,
        None, None, None, None, None, None, None, None,
    ]
    assignments = [
        "closing_price=excluded.closing_price",
        "total_shares=excluded.total_shares",
        "market_cap=excluded.market_cap",
        "pe_ratio=NULL",
        "pb_ratio=NULL",
        "dividend_yield=NULL",
        "enterprise_value=NULL",
        "fundamentals_period_end=NULL",
        "pe_earnings_basis=NULL",
        "pe_earnings_period_duration=NULL",
        "pe_earnings_section=NULL",
    ]
    return (
        f"INSERT INTO market_data ({', '.join(cols)}) VALUES "
        f"({', '.join(sql_value(v) for v in values)}) "
        f"ON CONFLICT(ticker, date) DO UPDATE SET {', '.join(assignments)};"
    )


def write_sql(rows: list[MarketRow], ticker: str | None) -> Path:
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{ticker.upper()}" if ticker else ""
    out = SQL_DIR / f"market_data{suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
    with out.open("w") as f:
        f.write("-- Step 16 market_data price/market-cap refresh\n")
        f.write(f"-- Generated: {datetime.now().isoformat()}\n")
        f.write("-- PSX DPS row shape: [timestamp, close, volume, open]\n\n")
        for row in rows:
            f.write(insert_sql(row) + "\n")
    return out


def upload(sql_path: Path) -> bool:
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", f"--file={sql_path}"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr[-2000:], file=sys.stderr)
        return False
    print(result.stdout[-2000:])
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 16 — publish market price and market cap")
    parser.add_argument("--ticker", help="Single ticker; default is full universe")
    parser.add_argument("--tickers", help="Comma-separated ticker list for scoped batch refresh")
    parser.add_argument("--push-d1", action="store_true", help="Upload generated SQL to remote D1")
    parser.add_argument("--limit", type=int, help="Limit tickers for rehearsal")
    parser.add_argument("--sleep", type=float, default=0.4, help="Seconds before each PSX ticker fetch")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent ticker fetches; keep low to avoid PSX throttling")
    args = parser.parse_args()

    tickers = load_tickers(args.ticker, args.tickers)
    if args.limit:
        tickers = tickers[:args.limit]

    today = datetime.now(timezone.utc)
    rows_by_ticker: dict[str, MarketRow] = {}
    skipped_by_ticker: dict[str, dict] = {}

    def run_one(ticker: str) -> tuple[str, MarketRow | dict]:
        if args.sleep > 0:
            time.sleep(args.sleep)
        return ticker, collect_row(ticker, today)

    max_workers = 1 if args.ticker else max(1, args.workers)
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(run_one, ticker) for ticker in tickers]
        for future in as_completed(futures):
            completed += 1
            ticker, result = future.result()
            if isinstance(result, MarketRow):
                rows_by_ticker[ticker] = result
                print(
                    f"[{completed}/{len(tickers)}] {ticker}: {result.date} "
                    f"close={result.close} mcap={result.market_cap:.0f} {result.status}",
                    flush=True,
                )
            else:
                skipped_by_ticker[ticker] = result
                print(f"[{completed}/{len(tickers)}] {ticker}: {result.get('status')}", flush=True)

    rows: list[MarketRow] = []
    skipped: list[dict] = []
    for ticker in tickers:
        if ticker in rows_by_ticker:
            rows.append(rows_by_ticker[ticker])
        elif ticker in skipped_by_ticker:
            skipped.append(skipped_by_ticker[ticker])

    sql_path = write_sql(rows, args.ticker)
    did_upload = False
    if args.push_d1 and rows:
        did_upload = upload(sql_path)
        if not did_upload:
            return 1

    report = {
        "run_at": datetime.now().isoformat(),
        "ticker_filter": args.ticker.upper() if args.ticker else None,
        "total_tickers": len(tickers),
        "rows": len(rows),
        "workers": max_workers,
        "stale_rows": sum(1 for row in rows if row.status == "stale"),
        "skipped": skipped,
        "sql_path": str(sql_path),
        "pushed_d1": did_upload,
        "sample_rows": [asdict(row) for row in rows[:10]],
    }
    write_report("16_market_data", report)
    (SQL_DIR / "market_data_report.json").write_text(json.dumps(report, indent=2))
    print(f"SQL -> {sql_path}")

    emit_decision(
        "16_market_data",
        f"Prepared {len(rows):,} market price/market-cap rows; "
        f"{sum(1 for row in rows if row.status == 'stale'):,} stale; "
        f"{'pushed to D1' if did_upload else 'not pushed' if args.push_d1 else 'SQL-only'}.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
