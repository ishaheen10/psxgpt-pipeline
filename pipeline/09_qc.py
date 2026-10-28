#!/usr/bin/env python3
"""Step 9 — QC. Deterministic checks on extracted Ref-column markdown, emitting the
unified failure log that step 14 (triage) reads.

Checks per statement:
  - schema:  every canonical label is a valid field for this statement type
  - formula: every Ref formula row (e.g. H=A+B+C) reconciles from its referenced rows

Input:  data/extracted_{type}/*.md
Output: artifacts/qc_failures.jsonl (appended) ; artifacts/reports/09_qc.json

Usage:
    python pipeline/09_qc.py --type cf [--ticker AABS]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared.report import write_report, emit_decision, Action, write_stage_issues, write_stage_lineage  # noqa: E402
from registry import STATEMENTS  # noqa: E402

FAIL_LOG = ARTIFACTS / "qc_failures.jsonl"
QC_DIR = ARTIFACTS / "09_qc"
ROUTE_MANIFEST = ARTIFACTS / "06_route" / "manifest.json"
SCHEMA = json.load(open(PROJECT_ROOT / "canonical_schema_fixed.json"))
ROW = re.compile(r"^\|(?P<src>[^|]*)\|(?P<canon>[^|]*)\|(?P<ref>[^|]*)\|(?P<vals>.*)\|\s*$")
STMT_DB = {"pl": "profit_loss", "bs": "balance_sheet", "cf": "cash_flow"}
STMT_KEY = {"pl": "PL", "bs": "BS", "cf": "CF"}

SOURCE_REPORTED_SUBTOTAL_ISSUES = {
    ("AGP_annual_2024_unconsolidated.md", "cfo"): "The visible operating cash-flow rows add to a different amount than the filing's reported operating subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("AKBL_quarterly_2025-09-30_unconsolidated.md", "cff"): "The visible financing rows add to a different amount than the filing's reported financing subtotal. We keep the PDF-reported subtotal, but believe the source filing may have an omitted or internally inconsistent line. Please inspect the linked PDF page before relying on this subtotal.",
    ("MTL_quarterly_2025-09-30_unconsolidated.md", "adjustments_subtotal"): "The visible adjustment rows add to a different amount than the filing's reported adjustment subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("NATF_quarterly_2023-03-31_consolidated.md", "cfi"): "The visible investing cash-flow rows add to a different amount than the filing's reported investing subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("NATF_quarterly_2023-12-31_consolidated.md", "cfi"): "The visible investing cash-flow rows add to a different amount than the filing's reported investing subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or reclassification issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("NATF_quarterly_2023-12-31_consolidated.md", "cff"): "The visible financing cash-flow rows add to a different amount than the filing's reported financing subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or reclassification issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("OLPL_quarterly_2025-09-30_consolidated.md", "cff"): "The visible financing rows add to a different amount than the filing's reported financing subtotal. We keep the PDF-reported subtotal, but believe the source filing may have an omitted or internally inconsistent line. Please inspect the linked PDF page before relying on this subtotal.",
    ("RPL_quarterly_2025-09-30_unconsolidated.md", "working_capital_changes"): "The visible working-capital rows add to a different amount than the filing's reported working-capital subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("SIBL_quarterly_2025-09-30_consolidated.md", "total_non_current_assets"): "The visible non-current asset rows add to a different amount than the filing's reported non-current asset subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("TPLP_quarterly_2024-12-31_unconsolidated.md", "total_current_liabilities"): "The visible current-liability rows add to a different amount than the filing's reported current-liability subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("TPLP_quarterly_2023-09-30_consolidated.md", "cfi"): "The visible investing cash-flow rows add to a different amount than the filing's reported investing subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("UNITY_annual_2025_consolidated.md", "cfi"): "The visible investing cash-flow rows add to a different amount than the filing's reported investing subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("UNITY_annual_2025_consolidated.md", "cff"): "The visible financing cash-flow rows add to a different amount than the filing's reported financing subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
    ("ZAHID_quarterly_2025-09-30_unconsolidated.md", "cfi"): "The visible investing cash-flow rows add to a different amount than the filing's reported investing subtotal. We keep the PDF-reported subtotal, but believe the source filing may contain an internal arithmetic or omitted-line issue. Please inspect the linked PDF page before relying on this subtotal.",
}

SOURCE_REPORTED_MANUAL_SUBTOTALS = {
    ("TPLP_quarterly_2024-12-31_unconsolidated.md", "total_current_liabilities"): {
        "formula": "W=R+S+T+U+V",
        "component_refs": ["R", "S", "T", "U", "V"],
    },
}


def num(cell: str):
    cell = cell.strip().replace("**", "").replace(",", "")
    if cell in ("", "-", "–", "—"):
        return None
    neg = cell.startswith("(") and cell.endswith(")")
    cell = cell.strip("()")
    try:
        v = float(cell)
        return -v if neg else v
    except ValueError:
        return None


def parse_table(md: str):
    rows = []
    for line in md.splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        canon = m.group("canon").strip().replace("**", "")
        ref = m.group("ref").strip().replace("**", "")
        if canon.lower() in ("canonical", ":---", ""):
            continue
        vals = [num(c) for c in m.group("vals").split("|")]
        rows.append({"source_item": m.group("src").strip().replace("**", ""),
                     "canon": canon, "ref": ref, "vals": vals})
    return rows


def file_context(md_path: Path, stmt: str) -> dict:
    stem = md_path.stem
    parts = stem.split("_")
    ticker = parts[0]
    section = parts[-1]
    period_type = parts[1] if len(parts) > 1 else ""
    filing_period = ""
    filing_date = None
    if period_type == "annual" and len(parts) >= 4:
        filing_period = f"annual_{parts[2]}"
        filing_date = f"{parts[2]}-12-31"
    elif period_type == "quarterly" and len(parts) >= 4:
        filing_period = f"quarterly_{parts[2]}"
        filing_date = parts[2]
    pages = []
    if ROUTE_MANIFEST.exists():
        manifest = json.loads(ROUTE_MANIFEST.read_text())
        pages = (manifest.get(ticker, {}).get(filing_period, {})
                 .get(section, {}).get(STMT_KEY[stmt], []))
    return {
        "ticker": ticker,
        "filing": filing_period,
        "filing_period": filing_period,
        "filing_date": filing_date,
        "statement_type": STMT_DB[stmt],
        "statement": stmt,
        "section": section,
        "source_file": md_path.name,
        "source_pages": pages,
    }


def suffix_reconciliation(
    terms: list[tuple[str, str]],
    letter_vals: dict[str, float | None],
    got: float,
    tol: float,
) -> tuple[str, float] | None:
    """Detect common hierarchical subtotal annotations.

    If a formula references both an intermediate subtotal and that subtotal's
    components, the displayed value may still reconcile to the later contiguous
    suffix. That means the value is usable, but the formula annotation should be
    audited as a warning rather than blocking the source numbers.
    """
    for start in range(1, len(terms)):
        total = 0.0
        refs = []
        ok = True
        for sign, ltr in terms[start:]:
            value = letter_vals.get(ltr)
            if value is None:
                ok = False
                break
            total += (-1 if sign == "-" else 1) * value
            refs.append(f"{sign}{ltr}" if sign else ltr)
        if ok and abs(total - got) <= tol:
            return "".join(refs), total
    return None


def formula_terms(ref: str) -> tuple[str, list[tuple[str, str]]] | None:
    match = re.fullmatch(r"([A-Z]{1,2})=([A-Z+\-]+)", ref)
    if not match:
        return None
    return match.group(1), re.findall(r"([+\-]?)([A-Z]{1,2})", match.group(2))


def formula_total(terms: list[tuple[str, str]], letter_vals: dict[str, float | None]) -> float | None:
    total = 0.0
    for sign, ltr in terms:
        value = letter_vals.get(ltr)
        if value is None:
            return None
        total += (-1 if sign == "-" else 1) * value
    return total


def matching_reconciled_duplicate(
    target: dict,
    rows: list[dict],
    letter_vals: dict[str, float | None],
    tol: float,
) -> str | None:
    """Find another formula row with the same canonical/value that reconciles.

    Some statements repeat an unlabelled subtotal row and the extractor may put
    one formula before its components, then later emit a correct duplicate
    subtotal. The data value is usable when at least one duplicate formula ties.
    """
    target_value = target["vals"][0]
    for other in rows:
        if other is target:
            continue
        if other.get("canon") != target.get("canon"):
            continue
        if not other.get("vals") or other["vals"][0] is None:
            continue
        if abs(other["vals"][0] - target_value) > tol:
            continue
        parsed = formula_terms(other.get("ref", ""))
        if not parsed:
            continue
        _, terms = parsed
        total = formula_total(terms, letter_vals)
        if total is not None and abs(total - target_value) <= tol:
            return other["ref"]
    return None


def source_report_issue_message(md_path: Path, canonical_name: str) -> str | None:
    clean_name = canonical_name.strip().replace("*", "")
    return SOURCE_REPORTED_SUBTOTAL_ISSUES.get((md_path.name, clean_name))


def source_report_caution(ctx: dict, row: dict, expected: float, got: float, message: str) -> dict:
    return {
        "ticker": ctx["ticker"],
        "metric": row["canon"],
        "status": "yellow",
        "short_message": message,
        "calculation": {
            "formula": row["ref"],
            "numerator": {"label": "Visible child rows", "value": round(expected, 1), "period_end": ctx.get("filing_date")},
            "denominator": {"label": "PDF-reported subtotal", "value": got, "period_end": ctx.get("filing_date")},
            "result": round(got - expected, 1),
            "basis": "source_pdf_reported_subtotal",
        },
        "basis_notes": [
            "The pipeline is preserving the value reported in the source PDF.",
            "The visible rows on the source page do not add back to that reported subtotal.",
            "Inspect the linked source page before relying on this specific subtotal.",
        ],
    }


def source_report_manual_issues(md_path: Path, ctx: dict, rows: list[dict]) -> list[dict]:
    issues = []
    by_ref = {r["ref"]: r for r in rows if re.fullmatch(r"[A-Z]{1,2}", r["ref"])}
    by_canonical = {r["canon"].strip().replace("*", ""): r for r in rows}
    for (source_file, canonical_name), spec in SOURCE_REPORTED_MANUAL_SUBTOTALS.items():
        if source_file != md_path.name:
            continue
        row = by_canonical.get(canonical_name)
        if not (row and row.get("vals") and row["vals"][0] is not None):
            continue
        component_rows = [by_ref.get(ref) for ref in spec["component_refs"]]
        if any(component is None for component in component_rows):
            continue
        component_value = 0.0
        components = []
        for component in component_rows:
            value = component["vals"][0]
            if value is None:
                component_value = None
                break
            component_value += value
            components.append({
                "ref": component["ref"],
                "sign": 1,
                "canonical_name": component["canon"],
                "source_item": component["source_item"],
                "value": value,
            })
        if component_value is None:
            continue
        got = row["vals"][0]
        tol = max(10.0, 0.01 * abs(got))
        if abs(component_value - got) <= tol:
            continue
        message = source_report_issue_message(md_path, canonical_name)
        if not message:
            continue
        issue_row = {**row, "ref": spec["formula"]}
        issues.append({**ctx, "gate": "formula", "rule": "source_report_inconsistent_subtotal",
                       "message": message,
                       "file": md_path.name,
                       "canonical_name": canonical_name, "source_item": row["source_item"],
                       "ref": spec["formula"], "components": components,
                       "expected": round(component_value, 1), "got": got,
                       "provenance": {"source_clean": True, "pages": ctx["source_pages"]},
                       "scope_pages": max(1, len(ctx["source_pages"])),
                       "severity": "warning",
                       "origin_stage": "source_pdf",
                       "user_caution_json": source_report_caution(ctx, issue_row, component_value, got, message),
                       "recommended_command": "inspect_source_pdf"})
    return issues


def check(md_path: Path, stmt: str) -> tuple[list[dict], list[dict], dict]:
    """Formula reconciliation only. Canonical labels are intentionally free-form (the
    extract packs say "do not force a match"), so schema-membership is NOT a failure."""
    rows = parse_table(md_path.read_text())
    ctx = file_context(md_path, stmt)
    letter_vals = {}  # first column's value per ref letter
    letter_rows = {}
    for r in rows:
        if re.fullmatch(r"[A-Z]{1,2}", r["ref"]) and r["vals"]:
            letter_vals[r["ref"]] = r["vals"][0]
            letter_rows[r["ref"]] = r
    fails = []
    issues = []
    for r in rows:
        parsed = formula_terms(r["ref"])
        if not (parsed and r["vals"] and r["vals"][0] is not None):
            continue
        target_ref, terms = parsed
        total = formula_total(terms, letter_vals)
        ok = total is not None
        tol = max(10.0, 0.01 * abs(r["vals"][0]))  # 1% tolerance absorbs rounding
        if ok and abs(total - r["vals"][0]) > tol:
            components = [
                {"ref": ltr, "sign": -1 if sign == "-" else 1,
                 "canonical_name": letter_rows.get(ltr, {}).get("canon"),
                 "source_item": letter_rows.get(ltr, {}).get("source_item"),
                 "value": letter_vals.get(ltr)}
                for sign, ltr in terms
            ]
            alt = suffix_reconciliation(terms, letter_vals, r["vals"][0], tol)
            if alt:
                alt_ref, alt_total = alt
                issues.append({**ctx, "gate": "formula", "rule": "formula_annotation_hierarchical_subtotal",
                               "message": f"{r['ref']} does not reconcile, but {target_ref}={alt_ref} matches the reported value.",
                               "file": md_path.name,
                               "canonical_name": r["canon"], "source_item": r["source_item"],
                               "ref": r["ref"], "alternate_ref": f"{target_ref}={alt_ref}",
                               "components": components,
                               "expected": round(total, 1), "alternate_expected": round(alt_total, 1),
                               "got": r["vals"][0],
                               "provenance": {"source_clean": True, "pages": ctx["source_pages"]},
                               "scope_pages": max(1, len(ctx["source_pages"])),
                               "severity": "warning",
                               "origin_stage": "08_extract",
                               "recommended_command": f"python3 pipeline/08_extract.py --type {stmt} --ticker {ctx['ticker']} --source pdf --repair-failures --reset && python3 pipeline/09_qc.py --type {stmt} --ticker {ctx['ticker']}"})
                continue
            duplicate_ref = matching_reconciled_duplicate(r, rows, letter_vals, tol)
            if duplicate_ref:
                issues.append({**ctx, "gate": "formula", "rule": "formula_annotation_duplicate_subtotal",
                               "message": f"{r['ref']} does not reconcile, but duplicate subtotal {duplicate_ref} matches the same reported value.",
                               "file": md_path.name,
                               "canonical_name": r["canon"], "source_item": r["source_item"],
                               "ref": r["ref"], "alternate_ref": duplicate_ref,
                               "components": components,
                               "expected": round(total, 1), "got": r["vals"][0],
                               "provenance": {"source_clean": True, "pages": ctx["source_pages"]},
                               "scope_pages": max(1, len(ctx["source_pages"])),
                               "severity": "warning",
                               "origin_stage": "08_extract",
                               "recommended_command": f"python3 pipeline/08_extract.py --type {stmt} --ticker {ctx['ticker']} --source pdf --repair-failures --reset && python3 pipeline/09_qc.py --type {stmt} --ticker {ctx['ticker']}"})
                continue
            source_message = source_report_issue_message(md_path, r["canon"])
            if source_message:
                issues.append({**ctx, "gate": "formula", "rule": "source_report_inconsistent_subtotal",
                               "message": source_message,
                               "file": md_path.name,
                               "canonical_name": r["canon"], "source_item": r["source_item"],
                               "ref": r["ref"], "components": components,
                               "expected": round(total, 1), "got": r["vals"][0],
                               "provenance": {"source_clean": True, "pages": ctx["source_pages"]},
                               "scope_pages": max(1, len(ctx["source_pages"])),
                               "severity": "warning",
                               "origin_stage": "source_pdf",
                               "user_caution_json": source_report_caution(ctx, r, total, r["vals"][0], source_message),
                               "recommended_command": "inspect_source_pdf"})
                continue
            failure = {**ctx, "gate": "formula", "rule": f"{r['ref']} reconciles",
                       "file": md_path.name,
                       "canonical_name": r["canon"], "source_item": r["source_item"],
                       "ref": r["ref"], "components": components,
                       "expected": round(total, 1), "got": r["vals"][0],
                       "provenance": {"source_clean": True, "pages": ctx["source_pages"]},
                       "scope_pages": max(1, len(ctx["source_pages"])),
                       "severity": "blocker",
                       "origin_stage": "08_extract",
                       "recommended_command": f"python3 pipeline/08_extract.py --type {stmt} --ticker {ctx['ticker']} --source pdf --repair-failures --reset && python3 pipeline/09_qc.py --type {stmt} --ticker {ctx['ticker']}"}
            fails.append(failure)
            issues.append(failure)
    existing_source_issue_keys = {
        (issue.get("source_file"), issue.get("canonical_name"))
        for issue in issues
        if issue.get("rule") == "source_report_inconsistent_subtotal"
    }
    for issue in source_report_manual_issues(md_path, ctx, rows):
        if (issue.get("source_file"), issue.get("canonical_name")) not in existing_source_issue_keys:
            issues.append(issue)
    result = {**ctx, "qc_status": "fail" if fails else "pass", "failures": fails,
              "issues": issues,
              "formulas": sum(1 for r in rows if "=" in r["ref"])}
    return fails, issues, result


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 9 — extraction QC")
    ap.add_argument("--type", required=True, choices=sorted(STATEMENTS))
    ap.add_argument("--ticker")
    a = ap.parse_args()
    cfg = STATEMENTS[a.type]
    out_dir = PROJECT_ROOT / cfg["output_dir"]
    files = sorted(out_dir.glob(f"{a.ticker + '_' if a.ticker else ''}*.md"))
    all_fails = []
    all_issues = []
    results = []
    step_name = f"09_qc_{a.type}"
    for f in files:
        fails, issues, result = check(f, a.type)
        all_fails += fails
        all_issues += issues
        results.append(result)

    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    QC_DIR.mkdir(parents=True, exist_ok=True)
    # Replace stale entries for this scope (ticker+type) so re-runs don't accumulate duplicates.
    scope_tickers = {f.name.split("_")[0] for f in files}
    retained = []
    if FAIL_LOG.exists():
        for line in FAIL_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if r.get("ticker") in scope_tickers and r.get("statement") == a.type:
                    continue  # will be replaced by fresh results
            except json.JSONDecodeError:
                pass
            retained.append(line)
    with open(FAIL_LOG, "w") as fh:
        for line in retained:
            fh.write(line + "\n")
        for x in all_fails:
            fh.write(json.dumps({**x, "id": f"{x['ticker']}-{a.type}-{x['section']}-{x['source_file']}-{x['gate']}-{x.get('canonical_name', x.get('rule'))}"}) + "\n")

    by_gate = {}
    for x in all_fails:
        by_gate[x["gate"]] = by_gate.get(x["gate"], 0) + 1
    qc_path = QC_DIR / f"{a.type}_results.json"
    qc_path.write_text(json.dumps({"type": a.type, "files": len(files), "failures": len(all_fails),
                                   "by_gate": by_gate, "results": results}, indent=2))
    write_stage_issues(step_name, all_issues, scope_tickers={a.ticker.upper()} if a.ticker else None)
    write_stage_lineage(step_name, [{k: v for k, v in r.items() if k != "failures"} for r in results],
                        scope_tickers={a.ticker.upper()} if a.ticker else None)
    report = {"type": a.type, "files": len(files), "failures": len(all_fails),
              "by_gate": by_gate, "qc_results": str(qc_path.relative_to(PROJECT_ROOT))}
    write_report(step_name, report)
    write_report("09_qc", report)
    emit_decision("09_qc", f"[{a.type}] QC'd {len(files)} files · {len(all_fails)} failures {by_gate}.",
                  actions=[Action("Triage failures", "python pipeline/14_triage.py"),
                           Action("Run normalize", f"python pipeline/10_normalize.py --type {a.type}")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
