#!/usr/bin/env python3
"""Step 13b — Push flattened data to Cloudflare D1.

Reads data/flat/{pl,bs,cf}.jsonl, generates SQL INSERT statements, and uploads
to the production D1 database (psx) in batches via wrangler.

Requires CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in .env (no wrangler login needed).

Usage:
    python pipeline/13_d1_push.py                       # all three types, full replace
    python pipeline/13_d1_push.py --type pl             # P&L only
    python pipeline/13_d1_push.py --ticker AABS         # single ticker across all types
    python pipeline/13_d1_push.py --quality-flags       # upload Step 19 data_quality_flags
    python pipeline/13_d1_push.py --sql-only            # generate SQL without uploading
    python pipeline/13_d1_push.py --batch-size 300      # smaller batches (default 500)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared.report import write_report, emit_decision  # noqa: E402

FLAT_DIR = PROJECT_ROOT / "data" / "flat"
QUALITY_DIR = ARTIFACTS / "quality"
SCS_RATIO_QUALITY_FLAGS = ARTIFACTS / "16_scs_ratio_qc" / "data_quality_flags.jsonl"
SQL_DIR = ARTIFACTS / "13_d1_push"
D1_DB = "psx"
TABLE = "financial_statements"
QUALITY_TABLE = "data_quality_flags"

COLUMNS = [
    "ticker", "company_name", "industry", "unit_type", "period_type",
    "period_end", "period_duration", "fiscal_year", "section",
    "statement_type", "canonical_name", "original_name", "value",
    "method", "source_file", "source_pages", "source_url", "qc_flag",
]

QUALITY_COLUMNS = [
    "flag_id", "ticker", "severity", "status", "category",
    "affected_table", "failed_stage", "origin_stage", "statement_type",
    "filing_period", "period_end", "period_duration", "section", "metric",
    "basis", "source_file", "source_pages", "rule", "expected", "got",
    "value", "message", "recommended_command", "user_caution_json", "generated_at",
]


def escape(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def make_insert(row: dict) -> str:
    vals = []
    for col in COLUMNS:
        v = row.get(col)
        if col == "source_pages" and isinstance(v, list):
            v = json.dumps(v)
        vals.append(escape(v))
    cols = ", ".join(f'"{c}"' for c in COLUMNS)
    return f"INSERT INTO {TABLE} ({cols}) VALUES ({', '.join(vals)});"


def make_quality_insert(row: dict) -> str:
    vals = []
    for col in QUALITY_COLUMNS:
        v = row.get(col)
        if col in {"source_pages", "user_caution_json"} and isinstance(v, (list, dict)):
            v = json.dumps(v)
        vals.append(escape(v))
    cols = ", ".join(f'"{c}"' for c in QUALITY_COLUMNS)
    return f"INSERT INTO {QUALITY_TABLE} ({cols}) VALUES ({', '.join(vals)});"


def generate_sql(stmt_type: str, ticker_filter: str | None) -> tuple[Path, int]:
    src = FLAT_DIR / f"{stmt_type}.jsonl"
    if not src.exists():
        print(f"  {stmt_type}: {src} not found, skipping")
        return Path(), 0

    SQL_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{ticker_filter}" if ticker_filter else ""
    out = SQL_DIR / f"{stmt_type}{suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"

    count = 0
    with out.open("w") as f:
        f.write(f"-- D1 upload: {TABLE} ({stmt_type})\n")
        f.write(f"-- Generated: {datetime.now().isoformat()}\n\n")

        if ticker_filter:
            f.write(f"DELETE FROM {TABLE} WHERE ticker = '{ticker_filter}' AND statement_type = '{_stmt_db_name(stmt_type)}';\n\n")
        else:
            f.write(f"DELETE FROM {TABLE} WHERE statement_type = '{_stmt_db_name(stmt_type)}';\n\n")

        for line in src.open():
            if not line.strip():
                continue
            row = json.loads(line)
            if ticker_filter and row.get("ticker", "").upper() != ticker_filter.upper():
                continue
            f.write(make_insert(row) + "\n")
            count += 1
            if count % 25000 == 0:
                print(f"    {count:,} rows...")

    print(f"  Generated {out.name} ({count:,} rows)")
    return out, count


def generate_quality_sql(ticker_filter: str | None) -> tuple[Path, int]:
    sources = [
        QUALITY_DIR / "data_quality_flags.jsonl",
        SCS_RATIO_QUALITY_FLAGS,
    ]
    existing_sources = [src for src in sources if src.exists()]
    if not existing_sources:
        print(f"  quality: no quality flag sources found, skipping")
        return Path(), 0

    SQL_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{ticker_filter}" if ticker_filter else ""
    out = SQL_DIR / f"data_quality_flags{suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"

    count = 0
    with out.open("w") as f:
        f.write(f"-- D1 upload: {QUALITY_TABLE}\n")
        f.write(f"-- Generated: {datetime.now().isoformat()}\n\n")
        f.write(f"DROP TABLE IF EXISTS {QUALITY_TABLE};\n")
        f.write(f"""
CREATE TABLE {QUALITY_TABLE} (
  flag_id TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  severity TEXT NOT NULL,
  status TEXT NOT NULL,
  category TEXT,
  affected_table TEXT,
  failed_stage TEXT,
  origin_stage TEXT,
  statement_type TEXT,
  filing_period TEXT,
  period_end TEXT,
  period_duration TEXT,
  section TEXT,
  metric TEXT,
  basis TEXT,
  source_file TEXT,
  source_pages TEXT,
  rule TEXT,
  expected REAL,
  got REAL,
  value REAL,
  message TEXT,
  recommended_command TEXT,
  user_caution_json TEXT,
  generated_at TEXT
);
CREATE INDEX idx_data_quality_flags_scope
  ON {QUALITY_TABLE}(ticker, affected_table, period_end, period_duration, statement_type, section, metric);
CREATE INDEX idx_data_quality_flags_ticker_severity
  ON {QUALITY_TABLE}(ticker, severity, status);

""")

        seen = set()
        for src in existing_sources:
            f.write(f"-- Source: {src.relative_to(PROJECT_ROOT)}\n")
            for line in src.open():
                if not line.strip():
                    continue
                row = json.loads(line)
                if ticker_filter and row.get("ticker", "").upper() != ticker_filter.upper():
                    continue
                flag_id = row.get("flag_id")
                if flag_id in seen:
                    continue
                seen.add(flag_id)
                f.write(make_quality_insert(row) + "\n")
                count += 1

    print(f"  Generated {out.name} ({count:,} flags from {len(existing_sources)} source file(s))")
    return out, count


def _stmt_db_name(t: str) -> str:
    return {"pl": "profit_loss", "bs": "balance_sheet", "cf": "cash_flow"}[t]


def upload(sql_path: Path) -> bool:
    size_mb = sql_path.stat().st_size / 1_048_576
    print(f"  Uploading {sql_path.name} ({size_mb:.1f} MB) as single file...")
    res = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", f"--file={sql_path}"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    if res.returncode != 0:
        print(f"  FAILED: {res.stderr[-2000:]}")
        return False
    print("  Upload complete.")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 13b — push flat data to D1")
    ap.add_argument("--type", choices=["pl", "bs", "cf"], help="default: all three")
    ap.add_argument("--ticker")
    ap.add_argument("--quality-flags", action="store_true", help="upload Step 19 data_quality_flags table")
    ap.add_argument("--sql-only", action="store_true", help="generate SQL without uploading")
    a = ap.parse_args()

    if a.quality_flags:
        ticker = a.ticker.upper() if a.ticker else None
        print("\n[QUALITY FLAGS]")
        sql_path, rows = generate_quality_sql(ticker)
        if rows == 0:
            return 1
        if not a.sql_only and not upload(sql_path):
            return 1
        write_report("13_d1_push_quality_flags", {
            "total_rows": rows,
            "sql_only": a.sql_only,
            "ticker_filter": ticker,
        })
        emit_decision("13_d1_push_quality_flags", f"Pushed {rows:,} quality flags to D1.")
        return 0

    types = [a.type] if a.type else ["pl", "bs", "cf"]
    ticker = a.ticker.upper() if a.ticker else None

    total_rows = 0
    failed = []

    for t in types:
        print(f"\n[{t.upper()}]")
        sql_path, rows = generate_sql(t, ticker)
        if rows == 0:
            continue
        total_rows += rows
        if a.sql_only:
            print(f"  --sql-only: skipping upload")
            continue
        if not upload(sql_path):
            failed.append(t)

    if failed:
        print(f"\nFailed types: {failed}")
        return 1

    write_report("13_d1_push", {"types": types, "total_rows": total_rows,
                                "sql_only": a.sql_only, "ticker_filter": ticker})
    emit_decision("13_d1_push", f"Pushed {total_rows:,} rows to D1 ({', '.join(types)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
