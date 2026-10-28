#!/usr/bin/env python3
"""Step 5 — Fix. Re-parse the pages that step 4 flagged ReOCR, then they re-tag.

This is the parse↔tag loop made explicit: a page the tagger couldn't read is sent back
to Gemini once; survivors of the cap are quarantined, not silently passed.

Input:  review/<...>.jsonl  (extraction_score == "ReOCR")
Output: re-parsed markdown_pages/<...>.md ; artifacts/reports/05_fix.json

Usage:
    python pipeline/05_fix.py [--ticker LUCK] [--max-attempts 2]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import PROJECT_ROOT  # noqa: E402
from shared.report import write_report, emit_decision, Action  # noqa: E402

REVIEW_ROOT = PROJECT_ROOT / "review"
PARSE = "pipeline/02_parse.py"


def reocr_pages(ticker: str | None, filing: str | None) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for jl in REVIEW_ROOT.rglob("*.jsonl"):
        for line in jl.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("extraction_score") != "ReOCR":
                continue
            rp = d["relative_path"]
            fil = "/".join(rp.split("/")[:3])
            if ticker and rp.split("/")[0].upper() != ticker.upper():
                continue
            if filing and fil != filing:
                continue
            out[fil].append(int("".join(c for c in rp.split("/")[-1] if c.isdigit())))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 5 — fix (re-parse ReOCR pages)")
    ap.add_argument("--ticker")
    ap.add_argument("--filing", help="scope to one TICKER/YEAR/FILING")
    ap.add_argument("--limit", type=int, help="cap filings re-parsed (safety)")
    ap.add_argument("--max-attempts", type=int, default=2)
    a = ap.parse_args()

    flagged = reocr_pages(a.ticker, a.filing)
    if a.limit:
        flagged = dict(list(flagged.items())[: a.limit])
    n_pages = sum(len(v) for v in flagged.values())
    if not n_pages:
        emit_decision("05_fix", "No ReOCR-flagged pages. Nothing to fix.")
        return 0

    reparsed = 0
    for filing, pages in flagged.items():
        pages_arg = ",".join(str(p) for p in sorted(pages))
        r = subprocess.run([sys.executable, PARSE, "--filing", filing, "--pages", pages_arg, "--reset"],
                           cwd=PROJECT_ROOT)
        if r.returncode == 0:
            reparsed += len(pages)

    write_report("05_fix", {"flagged_pages": n_pages, "filings": len(flagged), "reparsed": reparsed})
    # re-tag the affected filings (their review rows are now stale)
    retag = "python pipeline/04_tag.py --reset" + (f" --ticker {a.ticker}" if a.ticker else "")
    emit_decision("05_fix", f"Re-parsed {reparsed}/{n_pages} ReOCR pages across {len(flagged)} filings.",
                  actions=[Action("Re-tag affected filings", retag)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
