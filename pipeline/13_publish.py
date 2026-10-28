#!/usr/bin/env python3
"""Step 13 — Publish. Load flattened rows into the database. D1 IS SQLite, so the test
target is a local SQLite file; production points the same loader at D1.

Pre-upload QC (required fields, value type) runs first; bad rows are skipped and logged
to artifacts/qc_failures.jsonl rather than published.

Input:  data/flat/{pl,bs,cf}.jsonl
Output: <db> (default psx_test.db) ; artifacts/reports/13_publish.json

Usage:
    python pipeline/13_publish.py --db psx_test.db --ticker AABS
    python pipeline/13_publish.py --db psx_test.db          # all
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared.report import write_report, emit_decision, Action  # noqa: E402

FLAT_DIR = PROJECT_ROOT / "data" / "flat"
FAIL_LOG = ARTIFACTS / "qc_failures.jsonl"
TABLE = "financial_statements"
COLUMNS = ["ticker", "company_name", "industry", "unit_type", "period_type", "period_end",
           "period_duration", "fiscal_year", "section", "statement_type", "canonical_name",
           "original_name", "value", "method", "source_file", "source_pages", "source_url", "qc_flag"]
REQUIRED = ["ticker", "period_end", "statement_type", "canonical_name", "value"]

DDL = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
  {', '.join(f'"{c}" TEXT' if c not in ('value','fiscal_year') else f'"{c}" REAL' for c in COLUMNS)}
);
CREATE INDEX IF NOT EXISTS idx_fs_ticker ON {TABLE}(ticker);
CREATE INDEX IF NOT EXISTS idx_fs_lookup ON {TABLE}(ticker, statement_type, period_end);"""


def valid_row(r: dict) -> str | None:
    for f in REQUIRED:
        if r.get(f) in (None, ""):
            return f"missing required field {f}"
    if not isinstance(r.get("value"), (int, float)):
        return "value not numeric"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 13 — publish flat rows to (SQLite) DB")
    ap.add_argument("--db", default="psx_test.db")
    ap.add_argument("--ticker")
    ap.add_argument("--type", choices=["pl", "bs", "cf"], help="default: all three")
    ap.add_argument("--reset", action="store_true", help="drop the table first")
    a = ap.parse_args()

    db_path = PROJECT_ROOT / a.db
    con = sqlite3.connect(db_path)
    if a.reset:
        con.execute(f"DROP TABLE IF EXISTS {TABLE}")
    con.executescript(DDL)
    if a.ticker:
        con.execute(f"DELETE FROM {TABLE} WHERE ticker = ?", (a.ticker.upper(),))

    types = [a.type] if a.type else ["pl", "bs", "cf"]
    inserted = skipped = 0
    placeholders = ", ".join("?" for _ in COLUMNS)
    cols_sql = ", ".join(f'"{c}"' for c in COLUMNS)
    with open(FAIL_LOG, "a") as flog:
        for t in types:
            fp = FLAT_DIR / f"{t}.jsonl"
            if not fp.exists():
                continue
            batch = []
            for line in fp.open():
                if not line.strip():
                    continue
                r = json.loads(line)
                if a.ticker and r.get("ticker", "").upper() != a.ticker.upper():
                    continue
                err = valid_row(r)
                if err:
                    skipped += 1
                    flog.write(json.dumps({"gate": "preupload", "rule": err, "ticker": r.get("ticker"),
                                           "statement": r.get("statement_type"), "scope_pages": 1,
                                           "provenance": {"source_clean": True}}) + "\n")
                    continue
                row = [json.dumps(r[c]) if c == "source_pages" and isinstance(r.get(c), list) else r.get(c) for c in COLUMNS]
                batch.append(row)
            con.executemany(f"INSERT INTO {TABLE} ({cols_sql}) VALUES ({placeholders})", batch)
            inserted += len(batch)
    con.commit()
    total = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    con.close()

    write_report("13_publish", {"db": str(db_path.name), "inserted": inserted,
                                "skipped": skipped, "table_rows": total})
    emit_decision("13_publish", f"Published {inserted:,} rows to {db_path.name} "
                  f"({skipped} skipped pre-upload QC). Table now holds {total:,} rows.",
                  actions=[Action("Query check", f"sqlite3 {a.db} 'SELECT ticker,COUNT(*) FROM {TABLE} GROUP BY ticker'")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
