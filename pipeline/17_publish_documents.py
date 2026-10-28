#!/usr/bin/env python3
"""Step 17 — Publish filing-page corpus rows.

Builds `financial_documents_new` + `filings_fts_new` rows from `review/` +
`markdown_pages/` and optionally pushes them to D1. Source assets (page PDFs +
thumbnails) are published separately by pipeline/18_publish_assets.py.

Usage:
    python3 pipeline/17_publish_documents.py --ticker FECTC --push-d1
    python3 pipeline/17_publish_documents.py --gap --push-d1   # all tickers missing from D1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()
from shared.constants import PROJECT_ROOT, ARTIFACTS, MARKDOWN_ROOT  # noqa: E402
from shared.report import write_report, emit_decision, Action  # noqa: E402

REVIEW_ROOT = PROJECT_ROOT / "review"
SQL_DIR = ARTIFACTS / "17_publish_documents"
D1_DB = "psx"
TABLE = "financial_documents_new"
FTS_TABLE = "filings_fts_new"
DATA_ROOT = PROJECT_ROOT / "data"

DOC_COLUMNS = [
    "ticker",
    "industry",
    "filing_type",
    "filing_period",
    "filing_year",
    "section_tags",
    "pg",
    "jpg_path",
    "search_text",
    "text",
]


def escape(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def load_industries() -> dict[str, str]:
    path = PROJECT_ROOT / "tickers100.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text())
    return {
        str(row.get("Symbol", "")).strip().upper(): str(row.get("Industry", "")).strip()
        for row in rows
        if row.get("Symbol")
    }


def parse_page_path(relative_path: str) -> tuple[str, int, str, int]:
    parts = Path(relative_path).parts
    if len(parts) != 4:
        raise ValueError(f"Unexpected review relative_path: {relative_path}")
    ticker, year, filing, page_file = parts
    match = re.match(r"page_(\d+)\.md$", page_file)
    if not match:
        raise ValueError(f"Unexpected page filename: {relative_path}")
    return ticker.upper(), int(year), filing, int(match.group(1))


def _annual_period_lookup() -> dict[tuple[str, int], str]:
    """Return fiscal period end for annual filings from normalized statements.

    The local filing filename only carries `Annual_YYYY`. For non-December
    fiscal year-end companies, using `YYYY-12-31` mislabels the document corpus.
    Statement normalization has already parsed the period headers, so treat it
    as the authoritative source when available.
    """
    periods: dict[tuple[str, int], set[str]] = {}
    for stmt_type in ("pl", "bs", "cf"):
        json_dir = DATA_ROOT / f"json_{stmt_type}"
        if not json_dir.exists():
            continue
        for path in json_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            ticker = str(data.get("ticker") or path.stem).upper()
            for period in data.get("periods") or []:
                if str(period.get("period_type") or "").lower() != "annual":
                    continue
                source_file = str(period.get("source_file") or "").lower()
                match = re.match(rf"{re.escape(ticker.lower())}_annual_(\d{{4}})_", source_file)
                if not match:
                    continue
                period_end = str(period.get("period_end") or "")
                if re.match(r"^20\d{2}-\d{2}-\d{2}$", period_end):
                    periods.setdefault((ticker, int(match.group(1))), set()).add(period_end)

    lookup: dict[tuple[str, int], str] = {}
    for key, values in periods.items():
        # Annual reports usually include current-year and prior-year comparative
        # columns from the same source file. The filing's fiscal period is the
        # latest period_end parsed from that Annual_YYYY source, not the PSX
        # announcement/publication date and not always December.
        lookup[key] = max(values)
    return lookup


ANNUAL_PERIOD_LOOKUP = _annual_period_lookup()


def filing_meta(ticker: str, filing: str, year: int) -> tuple[str, str, str]:
    quarterly = re.search(r"Quarterly_(\d{4}-\d{2}-\d{2})", filing, re.I)
    if quarterly:
        return "quarterly", quarterly.group(1), "filename_quarterly_date"
    annual = re.search(r"Annual_(\d{4})", filing, re.I)
    if annual:
        annual_year = int(annual.group(1))
        inferred = ANNUAL_PERIOD_LOOKUP.get((ticker.upper(), annual_year))
        if inferred:
            return "annual", inferred, "statement_period_end"
        return "annual", f"{annual_year}-12-31", "fallback_december_year"
    return "annual", f"{year}-12-31", "fallback_review_year"


# D1 rejects an over-long SQL statement (SQLITE_TOOBIG). A handful of garbled-parse pages
# carry pathologically large text; cap the stored text well under the limit. search_text
# (the BM25 field) is separate and unaffected, so retrieval quality is preserved.
MAX_TEXT_CHARS = 60_000


def make_doc_row(row: dict, industries: dict[str, str]) -> dict:
    ticker, year, filing, page = parse_page_path(str(row["relative_path"]))
    filing_type, filing_period, _period_source = filing_meta(ticker, filing, year)
    md_rel = Path(row["relative_path"])
    markdown_path = MARKDOWN_ROOT / md_rel
    text = markdown_path.read_text() if markdown_path.exists() else ""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n\n[truncated]"
    jpg_path = str((Path(ticker) / str(year) / filing / f"page_{page:03d}.jpg")).replace(os.sep, "/")
    return {
        "ticker": ticker,
        "industry": industries.get(ticker, ""),
        "filing_type": filing_type,
        "filing_period": filing_period,
        "filing_year": year,
        "section_tags": json.dumps(row.get("section_tags", []), separators=(",", ":")),
        "pg": page,
        "jpg_path": jpg_path,
        "search_text": str(row.get("search_text") or ""),
        "text": text,
    }


def iter_review_rows(ticker: str | None):
    pattern = f"{ticker.upper()}/**/*.jsonl" if ticker else "**/*.jsonl"
    for review_path in sorted(REVIEW_ROOT.glob(pattern)):
        for line_no, line in enumerate(review_path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{review_path}:{line_no}: invalid JSON: {exc}") from exc
            relative_path = str(row.get("relative_path") or "")
            if ticker and not relative_path.upper().startswith(f"{ticker.upper()}/"):
                continue
            yield row


def collect_rows(ticker: str | None) -> list[dict]:
    industries = load_industries()
    rows = [make_doc_row(row, industries) for row in iter_review_rows(ticker)]
    rows.sort(key=lambda r: (r["ticker"], r["filing_year"], r["filing_period"], r["jpg_path"]))
    return rows


def make_insert(row: dict) -> str:
    cols = ", ".join(f'"{col}"' for col in DOC_COLUMNS)
    vals = ", ".join(escape(row.get(col)) for col in DOC_COLUMNS)
    return f"INSERT INTO {TABLE} ({cols}) VALUES ({vals});"


def generate_sql(rows: list[dict], ticker: str | None) -> Path:
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{ticker.upper()}" if ticker else ""
    out = SQL_DIR / f"financial_documents_new{suffix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sql"
    where = f"ticker = '{ticker.upper()}'" if ticker else "1 = 1"
    with out.open("w") as f:
        f.write(f"-- D1 upload: {TABLE}\n")
        f.write(f"-- Generated: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"-- Rows: {len(rows)}\n\n")
        f.write(f"DELETE FROM {FTS_TABLE} WHERE rowid IN (SELECT id FROM {TABLE} WHERE {where});\n")
        f.write(f"DELETE FROM {TABLE} WHERE {where};\n\n")
        for row in rows:
            f.write(make_insert(row) + "\n")
        f.write("\n")
        f.write(
            f"INSERT INTO {FTS_TABLE}(rowid, search_text, text) "
            f"SELECT id, search_text, text FROM {TABLE} WHERE {where};\n"
        )
    return out


def run_wrangler_d1(sql_path: Path) -> bool:
    res = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", f"--file={sql_path}"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if res.returncode != 0:
        print(res.stderr[-4000:])
        return False
    print(res.stdout[-2000:])
    return True


def gap_tickers() -> list[str]:
    """Tickers in financial_statements but missing from financial_documents_new (D1)."""
    sql = ("SELECT DISTINCT ticker FROM financial_statements WHERE ticker NOT IN "
           "(SELECT DISTINCT ticker FROM financial_documents_new) ORDER BY ticker")
    res = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", "--json", "--command", sql],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    if res.returncode != 0:
        raise SystemExit(f"gap query failed:\n{res.stderr[-1500:]}")
    data = json.loads(res.stdout[res.stdout.find("["):])
    rows = data[0]["results"] if isinstance(data, list) else data["result"][0]["results"]
    return [str(r["ticker"]).strip().upper() for r in rows if r.get("ticker")]


def publish_one(ticker: str | None, push_d1: bool) -> dict:
    rows = collect_rows(ticker)
    if not rows:
        return {"ticker": ticker, "document_rows": 0, "d1_pushed": False}
    sql_path = generate_sql(rows, ticker)
    pushed = run_wrangler_d1(sql_path) if push_d1 else False
    return {"ticker": ticker, "document_rows": len(rows),
            "sql_path": str(sql_path.relative_to(PROJECT_ROOT)), "d1_pushed": pushed}


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish financial_documents_new + filings_fts_new rows")
    ap.add_argument("--ticker", help="scope to one ticker")
    ap.add_argument("--gap", action="store_true",
                    help="publish every ticker in financial_statements missing from financial_documents_new")
    ap.add_argument("--push-d1", action="store_true", help="push generated SQL to remote D1")
    a = ap.parse_args()

    if a.push_d1 and not a.ticker and not a.gap:
        raise SystemExit("--push-d1 requires --ticker or --gap; refusing unscoped document-corpus replacement")

    # Source assets (page PDFs + thumbnails) are published by pipeline/18_publish_assets.py.
    if a.gap:
        tickers = gap_tickers()
        print(f"document gap: {len(tickers)} tickers")
        results, failures = [], []
        for i, t in enumerate(tickers, 1):
            r = publish_one(t, a.push_d1)
            results.append(r)
            ok = r["d1_pushed"] or not a.push_d1
            print(f"  [{i}/{len(tickers)}] {t}: rows={r['document_rows']} pushed={r['d1_pushed']}")
            if a.push_d1 and not r["d1_pushed"]:
                failures.append(t)
        write_report("17_publish_documents", {"mode": "gap", "tickers": len(tickers),
                                              "failed": failures})
        if failures:
            print(f"failed (re-run --gap to retry): {' '.join(failures)}")
            return 1
        return 0

    ticker = a.ticker.upper() if a.ticker else None
    r = publish_one(ticker, a.push_d1)
    if r["document_rows"] == 0:
        print("No document rows found")
        return 1
    if a.push_d1 and not r["d1_pushed"]:
        return 1
    write_report("17_publish_documents", r)
    emit_decision(
        "17_publish_documents",
        f"Prepared {r['document_rows']:,} document rows for {ticker or 'all tickers'}; D1 pushed={r['d1_pushed']}.",
        actions=[
            Action("Push scoped document rows", f"python3 pipeline/17_publish_documents.py --ticker {ticker or 'SYMBOL'} --push-d1"),
            Action("Upload source assets", f"python3 pipeline/18_publish_assets.py --tickers {ticker or 'SYMBOL'}"),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
