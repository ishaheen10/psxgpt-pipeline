#!/usr/bin/env python3
"""Audit annual-report announcements against filing corpus coverage.

This detector catches the HBL-style gap where PSX announcements contain an
annual-report attachment but the same report has not been promoted into the
financial filing corpus (`database_pdfs`, `financial_documents_new`, and
statement rows).

It keeps fiscal period end separate from announcement date. The announcement
date tells us when PSX published the attachment; the title/file name tells us
the fiscal period the report covers.

Usage:
    .venv/bin/python pipeline/21_audit_annual_reports.py --year 2025
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()
from shared.constants import PROJECT_ROOT  # noqa: E402
from shared.report import Action, emit_decision, write_report  # noqa: E402

D1_DB = "psx"
DB_PDFS = PROJECT_ROOT / "database_pdfs"
ANNOUNCEMENTS = PROJECT_ROOT / "announcement_documents"
OUT = PROJECT_ROOT / "artifacts" / "21_audit_annual_reports"


def d1_query(sql: str) -> list[dict]:
    res = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", "--json", "--command", sql],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise SystemExit(f"D1 query failed:\n{res.stderr[-3000:]}")
    data = json.loads(res.stdout[res.stdout.find("["):])
    return data[0]["results"] if isinstance(data, list) else data["result"][0]["results"]


def infer_period_end(title: str, year: int) -> tuple[str | None, str]:
    text = re.sub(r"\s+", " ", title.lower())
    escaped_year = re.escape(str(year))
    if re.search(rf"(?:12/31/{escaped_year}|31/12/{escaped_year}|{escaped_year}-12-31)", text):
        return f"{year}-12-31", "title_exact_date"
    if re.search(rf"(?:09/30/{escaped_year}|30/09/{escaped_year}|{escaped_year}-09-30)", text):
        return f"{year}-09-30", "title_exact_date"
    if re.search(rf"(?:06/30/{escaped_year}|30/06/{escaped_year}|{escaped_year}-06-30)", text):
        return f"{year}-06-30", "title_exact_date"
    if re.search(rf"(?:december|dec|31[- ]?12|31 december).*{escaped_year}", text):
        return f"{year}-12-31", "title_month_day"
    if re.search(rf"(?:september|sep|30[- ]?09|30-sep).*{escaped_year}", text):
        return f"{year}-09-30", "title_month_day"
    if re.search(rf"(?:june|jun|30[- ]?06|30 june).*{escaped_year}", text):
        return f"{year}-06-30", "title_month_day"
    if re.search(rf"year ended {escaped_year}\b", text):
        return f"{year}-12-31", "title_year_only_default_december"
    return None, "unparsed"


def file_id(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"(\d+)(?:-\d+)?\.(?:pdf|gif)$", url, re.I)
    return match.group(1) if match else None


def matching_announcement_files(ticker: str, published_year: int, url: str | None) -> list[str]:
    fid = file_id(url)
    root = ANNOUNCEMENTS / ticker / str(published_year)
    if not fid or not root.exists():
        return []
    return sorted(str(path.relative_to(PROJECT_ROOT)) for path in root.glob(f"*_{fid}.*"))


def local_annual_files(ticker: str, fiscal_year: int) -> list[str]:
    root = DB_PDFS / ticker / str(fiscal_year)
    if not root.exists():
        return []
    return sorted(str(path.relative_to(PROJECT_ROOT)) for path in root.glob(f"*Annual*{fiscal_year}*.pdf"))


def audit(year: int) -> dict:
    annual_announcements = d1_query(f"""
        SELECT ticker, announcement_date, announcement_time, title, direct_url, source_url
        FROM psx_announcements
        WHERE lower(title) LIKE '%annual report%'
          AND title LIKE '%{year}%'
        ORDER BY ticker, announcement_date, announcement_time
    """)
    doc_rows = d1_query(f"""
        SELECT ticker, filing_period, filing_year, COUNT(*) AS docs
        FROM financial_documents_new
        WHERE filing_type='annual' AND filing_year={year}
        GROUP BY ticker, filing_period, filing_year
        ORDER BY ticker, filing_period
    """)
    statement_rows = d1_query(f"""
        SELECT ticker, period_end, statement_type, period_duration, COUNT(*) AS rows
        FROM financial_statements
        WHERE period_end LIKE '{year}-%'
          AND (
            (statement_type='profit_loss' AND period_duration='12M') OR
            (statement_type='cash_flow' AND period_duration='12M') OR
            (statement_type='balance_sheet' AND period_duration='PIT')
          )
        GROUP BY ticker, period_end, statement_type, period_duration
        ORDER BY ticker, period_end, statement_type
    """)

    docs_by_period = {(row["ticker"], row["filing_period"]): int(row["docs"]) for row in doc_rows}
    docs_by_fiscal_year: dict[str, int] = defaultdict(int)
    doc_periods: dict[str, list[str]] = defaultdict(list)
    for row in doc_rows:
        docs_by_fiscal_year[row["ticker"]] += int(row["docs"])
        doc_periods[row["ticker"]].append(f"{row['filing_period']}:{row['docs']}")

    statements: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in statement_rows:
        statements[(row["ticker"], row["period_end"])][row["statement_type"]] += int(row["rows"])

    seen: set[tuple[str, str | None, str]] = set()
    cases = []
    for row in annual_announcements:
        ticker = str(row["ticker"]).upper()
        period_end, period_source = infer_period_end(str(row["title"]), year)
        url = row.get("direct_url") or row.get("source_url")
        key = (ticker, period_end, file_id(url) or str(row["title"]))
        if key in seen:
            continue
        seen.add(key)

        docs_at_period = docs_by_period.get((ticker, period_end), 0) if period_end else 0
        docs_for_year = docs_by_fiscal_year.get(ticker, 0)
        statement_counts = statements[(ticker, period_end)] if period_end else {}
        has_all_statements = bool(
            statement_counts.get("profit_loss")
            and statement_counts.get("balance_sheet")
            and statement_counts.get("cash_flow")
        )
        db_files = local_annual_files(ticker, year)
        announcement_files = matching_announcement_files(ticker, int(str(row["announcement_date"])[:4]), url)

        excluded_reason = excluded_instrument_reason(ticker)
        if excluded_reason:
            category = "excluded_instrument"
        elif not period_end:
            category = "manual_period_parse"
        elif docs_at_period == 0 and docs_for_year > 0:
            category = "docs_period_mismatch"
        elif docs_at_period == 0 and not has_all_statements:
            category = "missing_docs_and_statement_coverage"
        elif docs_at_period == 0:
            category = "missing_docs_only"
        elif not has_all_statements:
            category = "docs_present_statement_gap"
        else:
            category = "covered"

        cases.append({
            "category": category,
            "ticker": ticker,
            "period_end": period_end,
            "period_source": period_source,
            "announcement_date": row["announcement_date"],
            "announcement_time": row.get("announcement_time"),
            "title": row["title"],
            "direct_url": row.get("direct_url"),
            "source_url": row.get("source_url"),
            "docs_at_period": docs_at_period,
            "docs_for_fiscal_year": docs_for_year,
            "doc_periods_for_fiscal_year": doc_periods.get(ticker, []),
            "statement_rows": {
                "profit_loss_12m": int(statement_counts.get("profit_loss", 0)),
                "balance_sheet_pit": int(statement_counts.get("balance_sheet", 0)),
                "cash_flow_12m": int(statement_counts.get("cash_flow", 0)),
            },
            "excluded_instrument_reason": excluded_reason,
            "database_annual_files": db_files,
            "announcement_files": announcement_files,
            "recommended_command": recommended_command(category, ticker, period_end, bool(db_files), bool(announcement_files)),
        })

    order = {
        "excluded_instrument": -1,
        "missing_docs_and_statement_coverage": 0,
        "missing_docs_only": 1,
        "docs_period_mismatch": 2,
        "docs_present_statement_gap": 3,
        "manual_period_parse": 4,
        "covered": 5,
    }
    cases.sort(key=lambda item: (order.get(item["category"], 99), item["ticker"], item.get("period_end") or ""))
    summary = Counter(case["category"] for case in cases)
    return {
        "year": year,
        "annual_report_announcements": len(cases),
        "summary": dict(summary),
        "cases": cases,
    }


def recommended_command(category: str, ticker: str, period_end: str | None, has_db_pdf: bool, has_announcement_file: bool) -> str | None:
    if category == "excluded_instrument":
        return None
    if category == "docs_period_mismatch":
        return f".venv/bin/python pipeline/17_publish_documents.py --ticker {ticker} --push-d1 && .venv/bin/python pipeline/18_publish_assets.py --tickers {ticker}"
    if category == "missing_docs_only" and has_db_pdf:
        return f".venv/bin/python pipeline/01_ingest.py --split --ticker {ticker} && .venv/bin/python pipeline/02_parse.py --ticker {ticker} && .venv/bin/python pipeline/03_gate.py --ticker {ticker} && .venv/bin/python pipeline/04_tag.py --ticker {ticker} && .venv/bin/python pipeline/17_publish_documents.py --ticker {ticker} --push-d1 && .venv/bin/python pipeline/18_publish_assets.py --tickers {ticker}"
    if category == "missing_docs_and_statement_coverage" and has_announcement_file and not has_db_pdf:
        return f"promote annual announcement PDF for {ticker}, then run extract/documents chains"
    if category == "docs_present_statement_gap":
        return f".venv/bin/python pipeline/06_route.py --ticker {ticker} && .venv/bin/python pipeline/08_extract.py --type pl --ticker {ticker} --annual-only && .venv/bin/python pipeline/08_extract.py --type cf --ticker {ticker} --annual-only"
    if category == "manual_period_parse":
        return f"inspect announcement title for {ticker} and set fiscal period end explicitly"
    return None


def excluded_instrument_reason(ticker: str) -> str | None:
    symbol = ticker.upper()
    if "ETF" in symbol:
        return "etf"
    if symbol.endswith("TFC"):
        return "tfc"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit annual report announcement/file/statement coverage")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--fail-on-gaps", action="store_true")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    report = audit(args.year)
    out_path = OUT / f"annual_report_gaps_{args.year}.json"
    out_path.write_text(json.dumps(report, indent=2))
    write_report("21_audit_annual_reports", {
        "year": args.year,
        "annual_report_announcements": report["annual_report_announcements"],
        "summary": report["summary"],
        "report_path": str(out_path.relative_to(PROJECT_ROOT)),
    })
    print(json.dumps({
        "year": report["year"],
        "annual_report_announcements": report["annual_report_announcements"],
        "summary": report["summary"],
        "report_path": str(out_path.relative_to(PROJECT_ROOT)),
    }, indent=2))
    emit_decision(
        "21_audit_annual_reports",
        f"Audited {report['annual_report_announcements']} FY{args.year} annual-report announcements. "
        f"Gaps: {report['summary']}.",
        actions=[
            Action("Review gap detail", f"cat {out_path.relative_to(PROJECT_ROOT)}"),
            Action("Publish docs for docs-only gaps", "python3 pipeline/17_publish_documents.py --ticker <TICKER> --push-d1"),
        ],
    )
    has_gaps = any(k != "covered" and v for k, v in report["summary"].items())
    return 1 if args.fail_on_gaps and has_gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
