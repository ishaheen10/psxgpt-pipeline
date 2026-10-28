#!/usr/bin/env python3
"""Step 6 — Route. Turn tag output into the extraction manifest. Deterministic.

For each filing, group statement-tagged pages by (statement, basis), keep the
highest-confidence page(s) per slot. `basis: unknown` (standalone filers with no
consolidation label) is mapped to the single available set. Emits the same shape as the
legacy step2_statement_pages.json so the extractor reads it unchanged.

Input:  review/<...>.jsonl
Output: artifacts/06_route/manifest.json ; artifacts/reports/06_route.json

Usage:
    python pipeline/06_route.py [--ticker AABS] [--min-conf 0.5]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared.report import write_report, emit_decision, Action, write_stage_issues, write_stage_lineage  # noqa: E402

REVIEW_ROOT = PROJECT_ROOT / "review"
OUT = ARTIFACTS / "06_route"
TAG2KEY = {"profit_loss": "PL", "balance_sheet": "BS", "cash_flow": "CF",
           "comprehensive_income": "CI", "changes_in_equity": "SOCE"}
TICKERS_FILE = PROJECT_ROOT / "tickers100.json"


def load_industries() -> dict[str, str]:
    if not TICKERS_FILE.exists():
        return {}
    data = json.loads(TICKERS_FILE.read_text())
    return {
        str(row.get("Symbol", "")).strip().upper(): str(row.get("Industry", "")).strip()
        for row in data
        if row.get("Symbol")
    }


def is_insurance_ticker(ticker: str, industries: dict[str, str]) -> bool:
    industry = industries.get(ticker.upper(), "").lower()
    return "insurance" in industry or "takaful" in industry


def filing_period(filing: str) -> str:
    name = filing.split("/")[-1]
    parts = name.split("_")
    return f"{parts[1].lower()}_{'_'.join(parts[2:])}" if len(parts) >= 3 else name


def page_num(rel: str) -> int:
    return int("".join(c for c in rel.split("/")[-1] if c.isdigit()))


def is_statement_snapshot(row: dict) -> bool:
    """Reject directors' report snapshots as formal statement routes."""
    text = " ".join([
        str(row.get("relative_path") or ""),
        str(row.get("search_text") or ""),
    ]).lower()
    return (
        "directors" in text
        and "financial snapshot" in text
        and ("operating results" in text or "revenue increase" in text)
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 6 — route tags to extraction manifest")
    ap.add_argument("--ticker")
    ap.add_argument("--min-conf", type=float, default=0.5)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    scope_tickers = {a.ticker.upper()} if a.ticker else None
    industries = load_industries()

    files = sorted(REVIEW_ROOT.rglob("*.jsonl"))
    if a.ticker:
        files = [f for f in files if f.parts[-3].upper() == a.ticker.upper()]

    # manifest[ticker][period][basis][stmt_key] = [pages]
    manifest: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    slot_candidates: dict = defaultdict(list)  # (ticker,period,basis,key) -> [(conf,page)]
    slot_candidate_rows: dict = defaultdict(list)
    observed_filing_sections: set[tuple[str, str, str]] = set()
    counts = defaultdict(int)

    for jl in files:
        ticker, year, filing = jl.parts[-3], jl.parts[-2], jl.stem
        rel_filing = f"{ticker}/{year}/{filing}"
        period = filing_period(rel_filing)
        for line in jl.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            for t in d.get("section_tags", []):
                key = TAG2KEY.get(t.get("tag"))
                if not key or t.get("confidence", 0) < a.min_conf:
                    continue
                if key in {"PL", "BS"} and is_statement_snapshot(d):
                    continue
                basis = t.get("basis") or "unknown"
                # standalone filers carry no basis label; legacy convention keys them "consolidated"
                basis = "consolidated" if basis == "unknown" else basis
                slot_candidates[(ticker, period, basis, key)].append((t["confidence"], page_num(d["relative_path"])))
                slot_candidate_rows[(ticker, period, basis, key)].append({
                    "ticker": ticker,
                    "filing": filing,
                    "filing_year": int(year) if str(year).isdigit() else year,
                    "filing_period": period,
                    "section": basis,
                    "statement_key": key,
                    "statement_tag": t.get("tag"),
                    "page": page_num(d["relative_path"]),
                    "relative_path": d["relative_path"],
                    "confidence": t["confidence"],
                    "extraction_score": d.get("extraction_score"),
                })
                observed_filing_sections.add((ticker, period, basis))

    lineage = []
    for (ticker, period, basis, key), cands in slot_candidates.items():
        top = max(c[0] for c in cands)
        pages = sorted({p for c, p in cands if c >= top - 1e-9})  # highest-confidence page(s)
        manifest[ticker][period][basis][key] = pages
        counts[key] += 1
        candidates = sorted(slot_candidate_rows[(ticker, period, basis, key)], key=lambda r: (-r["confidence"], r["page"]))
        lineage.append({
            "ticker": ticker,
            "filing_period": period,
            "section": basis,
            "statement_key": key,
            "selected_pages": pages,
            "selected_relative_paths": [r["relative_path"] for r in candidates if r["page"] in pages],
            "candidate_pages": [{"page": r["page"], "confidence": r["confidence"], "relative_path": r["relative_path"]} for r in candidates],
            "rejected_pages": [{"page": r["page"], "confidence": r["confidence"], "relative_path": r["relative_path"]} for r in candidates if r["page"] not in pages],
            "selection_rule": "highest_confidence_with_ties",
            "origin_stage": "04_tag",
        })

    # Some insurance filings label the income statement as comprehensive income.
    # Step 08 consumes a PL route for profit/loss extraction, so expose CI as PL
    # only when the ticker is insurance and no explicit PL slot was selected.
    for ticker, periods in list(manifest.items()):
        if not is_insurance_ticker(ticker, industries):
            continue
        for period, bases in periods.items():
            for basis, slots in bases.items():
                if "PL" not in slots and "CI" in slots:
                    slots["PL"] = list(slots["CI"])
                    counts["PL"] += 1
                    lineage.append({
                        "ticker": ticker,
                        "filing_period": period,
                        "section": basis,
                        "statement_key": "PL",
                        "selected_pages": list(slots["PL"]),
                        "selected_relative_paths": [],
                        "candidate_pages": [{"page": page, "confidence": None, "relative_path": None} for page in slots["PL"]],
                        "rejected_pages": [],
                        "selection_rule": "insurance_ci_alias_when_pl_missing",
                        "origin_stage": "06_route",
                    })

    out = {tk: {pr: {ba: dict(keys) for ba, keys in bases.items()} for pr, bases in periods.items()}
           for tk, periods in manifest.items()}
    if scope_tickers and (OUT / "manifest.json").exists():
        existing = json.loads((OUT / "manifest.json").read_text())
        for ticker in scope_tickers:
            if ticker in out:
                existing[ticker] = out[ticker]
            else:
                existing.pop(ticker, None)
        out = existing
    (OUT / "manifest.json").write_text(json.dumps(out, indent=2))
    (OUT / "statement_pages.json").write_text(json.dumps(out, indent=2))

    issues = []
    for ticker, period, basis in sorted(observed_filing_sections):
        slots = out.get(ticker, {}).get(period, {}).get(basis, {})
        for key in ("PL", "BS", "CF"):
            if key not in slots:
                issues.append({
                    "severity": "warning",
                    "ticker": ticker,
                    "filing": period,
                    "filing_period": period,
                    "section": basis,
                    "statement_type": {"PL": "profit_loss", "BS": "balance_sheet", "CF": "cash_flow"}[key],
                    "rule": "missing_route_slot",
                    "message": f"No {key} route selected for {ticker} {period} {basis}",
                    "origin_stage": "04_tag",
                    "recommended_command": f"python3 pipeline/04_tag.py --ticker {ticker} --reset && python3 pipeline/06_route.py --ticker {ticker}",
                })
    write_stage_lineage("06_route", lineage, scope_tickers=scope_tickers)
    write_stage_issues("06_route", issues, scope_tickers=scope_tickers)

    n_filings = sum(len(p) for p in out.values())
    rep = {"tickers": len(out), "filing-periods": n_filings, "slots_by_statement": dict(counts),
           "lineage_rows": len(lineage), "issues": len(issues)}
    write_report("06_route", rep)
    emit_decision("06_route", f"Routed {len(out)} tickers / {n_filings} filing-periods. "
                  f"Statement slots: {dict(counts)}.",
                  actions=[Action("Run extract", "python pipeline/08_extract.py --type pl")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
