#!/usr/bin/env python3
"""Step 1 — Ingest. Report download coverage, synthesize a recommendation, and offer the
search-agent escape hatch for missing filings.

Output: artifacts/reports/01_ingest.json ; artifacts/01_ingest/missing.json (with --find-missing)

Usage:
    python pipeline/01_ingest.py                 # coverage report + recommendation
    python pipeline/01_ingest.py --find-missing  # emit the gap list for the search agent
    python pipeline/01_ingest.py --split         # split any new flat PDFs into pdf_pages/
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared.report import write_report, emit_decision, Action, llm_synthesis  # noqa: E402

PDF_ROOT = PROJECT_ROOT / "database_pdfs"
TICKERS = PROJECT_ROOT / "tickers100.json"
OUT = ARTIFACTS / "01_ingest"
ANNUAL_YEARS = range(2021, 2026)  # expected-annual baseline for gap detection


def present_filings() -> set[str]:
    out = set()
    for p in PDF_ROOT.glob("*/*/*.pdf"):
        parts = p.relative_to(PDF_ROOT).parts
        out.add(f"{parts[0]}/{parts[1]}/{p.stem}")
    return out


PDF_PAGES_ROOT = PROJECT_ROOT / "pdf_pages"


def split_pdfs(ticker_filter: str | None = None) -> int:
    """Split flat PDFs from database_pdfs/ into per-page PDFs in pdf_pages/.

    Skips filings that already have a directory in pdf_pages/ (idempotent).
    Returns number of pages written.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        print("pypdf not installed — run: pip install pypdf")
        return 0

    total_pages = 0
    skipped = 0
    for pdf_path in sorted(PDF_ROOT.rglob("*.pdf")):
        parts = pdf_path.relative_to(PDF_ROOT).parts
        if len(parts) != 3:
            continue
        ticker, year, filename = parts
        if ticker_filter and ticker.upper() != ticker_filter.upper():
            continue
        filing_stem = pdf_path.stem
        out_dir = PDF_PAGES_ROOT / ticker / year / filing_stem
        if out_dir.exists() and any(out_dir.glob("page_*.pdf")):
            skipped += 1
            continue
        try:
            reader = PdfReader(str(pdf_path))
            n = len(reader.pages)
            if n == 0:
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            for i, pg in enumerate(reader.pages, 1):
                writer = PdfWriter()
                writer.add_page(pg)
                page_path = out_dir / f"page_{i:03d}.pdf"
                with open(page_path, "wb") as f:
                    writer.write(f)
            total_pages += n
        except Exception as e:
            print(f"  [WARN] Could not split {pdf_path.name}: {e}")

    print(f"  Split: {total_pages:,} pages written, {skipped:,} filings already split")
    return total_pages


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 1 — ingest coverage + decision")
    ap.add_argument("--ticker"); ap.add_argument("--year")
    ap.add_argument("--find-missing", action="store_true", help="emit gap list for the search agent")
    ap.add_argument("--split", action="store_true", help="split flat PDFs in database_pdfs/ into pdf_pages/")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()

    if a.split:
        print("Splitting flat PDFs into pdf_pages/ ...")
        n = split_pdfs(ticker_filter=a.ticker)
        print(f"Done. {n:,} total pages written.")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)

    tickers = [t["Symbol"] for t in json.loads(TICKERS.read_text())] if TICKERS.exists() else []
    present = present_filings()
    present_tickers = {f.split("/")[0] for f in present}

    # expected-annual baseline -> missing annuals
    missing = []
    for tk in tickers:
        if a.ticker and tk.upper() != a.ticker.upper():
            continue
        for yr in ANNUAL_YEARS:
            key = f"{tk}/{yr}/{tk}_Annual_{yr}"
            if key not in present:
                missing.append({"ticker": tk, "year": yr, "type": "Annual"})

    per_ticker = defaultdict(int)
    for f in present:
        per_ticker[f.split("/")[0]] += 1

    rep = {"tickers_expected": len(tickers), "tickers_present": len(present_tickers),
           "filings_present": len(present), "missing_annuals": len(missing),
           "missing_detail": missing[:50]}
    write_report("01_ingest", rep)

    if a.find_missing:
        (OUT / "missing.json").write_text(json.dumps(missing, indent=2))
        print(f"\nWrote {len(missing)} missing filings to {OUT/'missing.json'}.")
        print("Search-agent task: for each, find the company investor-relations PDF URL, then:")
        print("  python pipeline/01_ingest.py --download  (after adding URLs to the downloader source list)")
        return 0

    summary = None if a.no_llm else llm_synthesis(
        rep, "Summarize PSX download coverage. Call out which missing annuals likely need an "
        "investor-relations search vs. are simply not filed yet. End with a one-line recommendation.")
    if not summary:
        summary = (f"{len(present)} filings present across {len(present_tickers)} tickers. "
                   f"{len(missing)} annuals absent vs the 2021–2025 baseline.")
    emit_decision("01_ingest", summary, actions=[
        Action("Find missing PDFs (search agent)", "python pipeline/01_ingest.py --find-missing"),
        Action("Proceed to parse", "python pipeline/02_parse.py --gap"),
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
