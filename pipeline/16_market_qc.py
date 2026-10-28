#!/usr/bin/env python3
"""Step 16 QC — compare D1 market_data to live PSX price/share inputs.

This QC stage matches the market-data contract used by serve:

- PSX DPS EOD rows are [timestamp, close, volume, open].
- market_data is authoritative for closing_price, total_shares, and market_cap.
- P/E, P/B, dividend_yield, and enterprise_value are not validated here; compute
  those from market cap plus sourced financial statement rows when needed.
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
ARTIFACTS_DIR = ARTIFACTS / "16_market_qc"
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
class QcResult:
    ticker: str
    status: str
    psx_date: str | None = None
    d1_date: str | None = None
    psx_close: float | None = None
    d1_close: float | None = None
    psx_shares: int | None = None
    d1_shares: int | None = None
    expected_market_cap: float | None = None
    d1_market_cap: float | None = None
    stale_days: int | None = None
    close_diff_pct: float | None = None
    market_cap_diff_pct: float | None = None
    notes: list[str] | None = None


def load_tickers(ticker: str | None, tickers_csv: str | None) -> list[str]:
    if ticker:
        return [ticker.upper()]
    if tickers_csv:
        return [part.strip().upper() for part in tickers_csv.split(",") if part.strip()]
    data = json.loads(TICKERS_FILE.read_text())
    return [str(row["Symbol"]).upper() for row in data]


def pct_diff(expected: float | None, actual: float | None) -> float | None:
    if expected is None or actual is None or expected == 0:
        return None
    return abs(actual - expected) / abs(expected) * 100


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
        return {
            "date": datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d"),
            "close": float(close),
            "volume": int(volume) if volume is not None else None,
            "open": float(open_price) if open_price is not None else None,
        }
    except Exception:
        return None


def d1_query(sql: str) -> list[dict]:
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", "--json", "--command", sql],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr[-2000:], file=sys.stderr)
        return []
    rows: list[dict] = []
    try:
        data = json.loads(result.stdout)
        if isinstance(data, list):
            for block in data:
                if isinstance(block, dict) and isinstance(block.get("results"), list):
                    rows.extend(block["results"])
    except Exception:
        pass
    return rows


def latest_d1_market_row(ticker: str) -> dict | None:
    safe = ticker.replace("'", "''")
    rows = d1_query(
        "SELECT ticker, date, closing_price, total_shares, market_cap "
        f"FROM market_data WHERE ticker='{safe}' ORDER BY date DESC LIMIT 1"
    )
    return rows[0] if rows else None


def check_ticker(ticker: str, threshold_pct: float, today: datetime) -> QcResult:
    price = fetch_latest_price(ticker)
    if not price or price["close"] <= 0:
        return QcResult(ticker=ticker, status="no_psx_price")

    shares = fetch_shares(ticker)
    if not shares or shares <= 0:
        return QcResult(
            ticker=ticker,
            status="no_psx_shares",
            psx_date=price["date"],
            psx_close=price["close"],
        )

    d1 = latest_d1_market_row(ticker)
    if not d1:
        return QcResult(
            ticker=ticker,
            status="missing_d1",
            psx_date=price["date"],
            psx_close=price["close"],
            psx_shares=shares,
            expected_market_cap=price["close"] * shares,
        )

    d1_close = float(d1["closing_price"]) if d1.get("closing_price") is not None else None
    d1_shares = int(d1["total_shares"]) if d1.get("total_shares") is not None else None
    d1_market_cap = float(d1["market_cap"]) if d1.get("market_cap") is not None else None
    expected_market_cap = price["close"] * shares
    close_diff = pct_diff(price["close"], d1_close)
    mcap_diff = pct_diff(expected_market_cap, d1_market_cap)
    psx_date = datetime.strptime(price["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    stale_days = (today - psx_date).days

    notes: list[str] = []
    if d1.get("date") != price["date"]:
        notes.append("d1_date_differs_from_psx_latest")
    if d1_shares != shares:
        notes.append("d1_shares_differ_from_psx_company_page")
    if stale_days > 30:
        notes.append("psx_latest_row_is_stale")
    if close_diff is not None and close_diff > threshold_pct:
        notes.append("closing_price_diff_above_threshold")
    if mcap_diff is not None and mcap_diff > threshold_pct:
        notes.append("market_cap_diff_above_threshold")

    status = "ok" if not notes or notes == ["psx_latest_row_is_stale"] else "flag"
    return QcResult(
        ticker=ticker,
        status=status,
        psx_date=price["date"],
        d1_date=str(d1.get("date") or ""),
        psx_close=price["close"],
        d1_close=d1_close,
        psx_shares=shares,
        d1_shares=d1_shares,
        expected_market_cap=expected_market_cap,
        d1_market_cap=d1_market_cap,
        stale_days=stale_days,
        close_diff_pct=round(close_diff, 4) if close_diff is not None else None,
        market_cap_diff_pct=round(mcap_diff, 4) if mcap_diff is not None else None,
        notes=notes,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 16 QC — market price and market cap")
    parser.add_argument("--ticker", help="Single ticker; default is full universe")
    parser.add_argument("--tickers", help="Comma-separated ticker list for scoped batch QC")
    parser.add_argument("--threshold", type=float, default=0.05, help="Percent deviation to flag (default 0.05)")
    parser.add_argument("--limit", type=int, help="Limit tickers for rehearsal")
    parser.add_argument("--sleep", type=float, default=0.4, help="Seconds before each PSX ticker check")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent ticker checks; keep low to avoid PSX throttling")
    args = parser.parse_args()

    tickers = load_tickers(args.ticker, args.tickers)
    if args.limit:
        tickers = tickers[:args.limit]

    today = datetime.now(timezone.utc)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    results_by_ticker: dict[str, QcResult] = {}

    print(f"Checking {len(tickers)} tickers against live PSX DPS close/share data...")

    def run_one(ticker: str) -> tuple[str, QcResult]:
        if args.sleep > 0:
            time.sleep(args.sleep)
        return ticker, check_ticker(ticker, args.threshold, today)

    max_workers = 1 if args.ticker else max(1, args.workers)
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(run_one, ticker) for ticker in tickers]
        for future in as_completed(futures):
            completed += 1
            ticker, result = future.result()
            results_by_ticker[ticker] = result
            suffix = ""
            if result.notes:
                suffix = " | " + ", ".join(result.notes)
            print(f"[{completed}/{len(tickers)}] {ticker}: {result.status}{suffix}", flush=True)

    results = [results_by_ticker[ticker] for ticker in tickers if ticker in results_by_ticker]

    flags = [row for row in results if row.status in {"flag", "missing_d1", "no_psx_price", "no_psx_shares"}]
    stale = [row for row in results if row.notes and "psx_latest_row_is_stale" in row.notes]
    report = {
        "run_at": datetime.now().isoformat(),
        "threshold_pct": args.threshold,
        "workers": max_workers,
        "total": len(results),
        "flags": len(flags),
        "stale_psx_rows": len(stale),
        "flagged_tickers": [row.ticker for row in flags],
        "results": [asdict(row) for row in results],
    }
    write_report("16_market_qc", report)
    report_path = ARTIFACTS_DIR / "market_qc.json"
    report_path.write_text(json.dumps(report, indent=2))

    print(f"\nMarket QC complete: {len(flags)} flags / {len(results)} checked")
    print(f"Report -> {report_path}")
    emit_decision(
        "16_market_qc",
        f"Checked {len(results):,} tickers; {len(flags):,} flagged; {len(stale):,} have stale PSX latest rows.",
    )
    return 1 if flags else 0


if __name__ == "__main__":
    raise SystemExit(main())
