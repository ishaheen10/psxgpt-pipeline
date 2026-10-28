#!/usr/bin/env python3
"""Step 14 — triage current extraction QC failures.

The current pipeline writes one issue ledger per QC stage:

    artifacts/issues/09_qc_pl.jsonl
    artifacts/issues/09_qc_bs.jsonl
    artifacts/issues/09_qc_cf.jsonl

This step reads those ledgers by default, groups failures into repair clusters,
and emits an operator queue with exact ticker / filing / statement / source-page
scope plus the command that re-enters the pipeline at the earliest bad stage.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOGS = [
    ROOT / "artifacts" / "issues" / "09_qc_pl.jsonl",
    ROOT / "artifacts" / "issues" / "09_qc_bs.jsonl",
    ROOT / "artifacts" / "issues" / "09_qc_cf.jsonl",
]
LEGACY_LOG = ROOT / "artifacts" / "qc_failures.jsonl"
OUT_DIR = ROOT / "artifacts" / "triage"

# Local price table (USD per 1M tokens). Cost is computed, never model-reported.
PRICES = {
    "gemini-3.1-flash-lite": {"in": 0.10, "out": 0.40},
    "deepseek-v4-pro": {"in": 0.435, "out": 0.87},
}
# rough tokens per page per re-entry stage (input+output), for estimation
STAGE_TOKENS = {"parse": (1200, 800, "gemini-3.1-flash-lite"),
                "review": (1700, 200, "deepseek-v4-pro"),
                "extract": (3500, 1500, "deepseek-v4-pro")}
AUTO_COST_CAP = 0.10  # $; clusters under this + high confidence + runtime layer auto-run

# re-entry stage cost = stage tokens (+ cascade) for the scope of pages
CASCADE = {"parse": ["parse", "review", "extract"], "extract": ["extract"], "review": ["review", "extract"]}


def diagnose(f: dict) -> tuple[str, str, str]:
    """Return (layer, re_entry_stage, confidence). Deterministic rules."""
    gate = f.get("gate")
    if gate == "parse":
        return "parse", "parse", "high"
    prov = f.get("provenance", {})
    if gate in ("formula", "semantic", "derived", "preupload"):
        if prov.get("source_clean") is False:
            return "parse", "parse", "high"          # bad source -> re-parse (cascades)
        if f.get("canonical_field") or f.get("canonical_name"):
            return "extraction", "extract", "med"     # mapping; may be systematic (see clustering)
        return "extraction", "extract", "high"
    return "unknown", "extract", "low"


def cluster_key(f: dict, layer: str) -> str:
    # group by diagnosed cause so one decision covers many items
    canonical = f.get("canonical_field") or f.get("canonical_name")
    statement = f.get("statement") or f.get("statement_type") or "statement"
    if canonical:
        return f"{layer}:{statement}:{f.get('rule', 'unknown')}:{canonical}"
    return f"{layer}:{f.get('rule', 'unknown')}"


def estimate_cost(re_entry: str, pages: int) -> float:
    total = 0.0
    for stage in CASCADE.get(re_entry, [re_entry]):
        tin, tout, model = STAGE_TOKENS[stage]
        p = PRICES[model]
        total += pages * (tin * p["in"] + tout * p["out"]) / 1_000_000
    return round(total, 4)


def load_failures(paths: list[Path]) -> list[dict]:
    failures: list[dict] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("severity") != "blocker":
                continue
            issue_id = str(row.get("issue_id") or row.get("id") or "")
            if issue_id and issue_id in seen:
                continue
            if issue_id:
                seen.add(issue_id)
            failures.append(row)
    return failures


def row_key(f: dict) -> tuple:
    return (
        str(f.get("ticker") or ""),
        str(f.get("filing_period") or f.get("filing") or ""),
        str(f.get("statement") or f.get("statement_type") or ""),
        str(f.get("section") or ""),
        str(f.get("canonical_name") or f.get("canonical_field") or ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="QC failure triage")
    ap.add_argument("--log", type=Path, action="append", help="issue JSONL to triage; defaults to current 09_qc ledgers")
    a = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_paths = a.log or [p for p in DEFAULT_LOGS if p.exists()]
    if not log_paths and LEGACY_LOG.exists():
        log_paths = [LEGACY_LOG]
    failures = load_failures(log_paths)

    if not failures:
        (OUT_DIR / "triage.md").write_text("# Triage\n\nNo QC failures logged. Nothing to decide.\n")
        (OUT_DIR / "recommended_actions.json").write_text("[]\n")
        (OUT_DIR / "open_qc_queue.jsonl").write_text("")
        print("No current QC failures. Wrote empty triage report.")
        return 0

    clusters: dict[str, list] = defaultdict(list)
    meta: dict[str, dict] = {}
    for f in failures:
        layer, re_entry, conf = diagnose(f)
        k = cluster_key(f, layer)
        clusters[k].append(f)
        meta.setdefault(k, {"layer": layer, "re_entry": re_entry, "conf": conf, "tickers": set()})
        meta[k]["tickers"].add(f.get("ticker", "?"))

    actions = []
    for k, items in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
        m = meta[k]
        tickers = m["tickers"]
        systematic = m["layer"] == "extraction" and (items[0].get("canonical_field") or items[0].get("canonical_name")) and len(tickers) >= 3
        pages = sum(int(i.get("scope_pages", 1)) for i in items)
        cost = estimate_cost(m["re_entry"], pages)
        if systematic:
            rec, layer = "fix_pack", "systematic"
        elif m["layer"] in ("parse", "extraction") and m["conf"] == "high" and cost < AUTO_COST_CAP:
            rec, layer = "auto_rerun", m["layer"]
        else:
            rec, layer = "ask_rerun", m["layer"]
        actions.append({"cluster": k, "layer": layer, "items": len(items),
                        "tickers": sorted(tickers), "re_entry": m["re_entry"],
                        "scope_pages": pages, "est_cost_usd": cost,
                        "confidence": m["conf"], "recommended": rec})

    (OUT_DIR / "recommended_actions.json").write_text(json.dumps(actions, indent=2))
    queue = []
    for f in sorted(failures, key=row_key):
        statement = f.get("statement") or ""
        ticker = str(f.get("ticker") or "").upper()
        default_cmd = f.get("recommended_command")
        if not default_cmd and statement in {"pl", "bs", "cf"} and ticker:
            default_cmd = (
                f"python3 pipeline/08_extract.py --type {statement} --ticker {ticker} "
                "--source pdf --repair-failures --reset && "
                f"python3 pipeline/09_qc.py --type {statement} --ticker {ticker}"
            )
        queue.append({
            "ticker": ticker,
            "filing_period": f.get("filing_period") or f.get("filing"),
            "period_end": f.get("filing_date") or f.get("period_end"),
            "statement": statement or f.get("statement_type"),
            "section": f.get("section"),
            "metric": f.get("canonical_name") or f.get("canonical_field"),
            "rule": f.get("rule"),
            "expected": f.get("expected"),
            "got": f.get("got"),
            "source_file": f.get("source_file") or f.get("file"),
            "source_pages": f.get("source_pages") or f.get("pages"),
            "origin_stage": f.get("origin_stage") or "08_extract",
            "failed_stage": f.get("stage") or "09_qc",
            "re_entry_stage": "08_extract",
            "recommended_command": default_cmd,
        })
    (OUT_DIR / "open_qc_queue.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in queue))
    auto = [x for x in actions if x["recommended"] == "auto_rerun"]
    ask = [x for x in actions if x["recommended"] != "auto_rerun"]
    lines = ["# Triage\n", f"{len(failures)} current QC blockers in {len(actions)} clusters.\n",
             f"Logs: {', '.join(str(p.relative_to(ROOT)) for p in log_paths)}\n",
             f"- auto-run (cheap, high-confidence): {len(auto)}",
             f"- need a decision: {len(ask)}\n", "## Open QC Queue\n"]
    lines.append("| Ticker | Filing | Statement | Section | Metric | Rule | Source pages | Rerun |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in queue:
        rule = str(row.get("rule") or "").replace("|", "/")
        cmd = str(row.get("recommended_command") or "").replace("|", "/")
        lines.append(
            f"| {row['ticker']} | {row.get('filing_period') or ''} | {row.get('statement') or ''} | "
            f"{row.get('section') or ''} | {row.get('metric') or ''} | {rule} | "
            f"{row.get('source_pages') or ''} | `{cmd}` |"
        )
    lines.append("\n## Clusters needing a decision\n")
    for x in ask:
        lines.append(f"- **{x['cluster']}** — {x['layer']}, {x['items']} items, "
                     f"{len(x['tickers'])} tickers, re-enter `{x['re_entry']}`, "
                     f"est ${x['est_cost_usd']} → recommend **{x['recommended']}**")
    (OUT_DIR / "triage.md").write_text("\n".join(lines) + "\n")
    print(f"{len(failures)} current QC blockers → {len(actions)} clusters ({len(auto)} auto, {len(ask)} ask). "
          f"Wrote {OUT_DIR}/triage.md and {OUT_DIR}/open_qc_queue.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
