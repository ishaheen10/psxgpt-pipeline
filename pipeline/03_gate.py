#!/usr/bin/env python3
"""Step 3 — Gate. Deterministic, free. Classify every parsed page; the survivors ARE
the tag manifest. No model calls.

First-match order (PIPELINE_SPEC §5): corrupted → edge → blank → empty_table →
image_only → heading_only → thin → urdu.

Input:  markdown_pages/<ticker>/<year>/<filing>/page_###.md
Output: artifacts/reports/03_gate.json
        artifacts/03_gate/skip_manifest.json   (page -> reason)
        artifacts/03_gate/tag_manifest.json     (surviving pages to tag)

Usage:
    python pipeline/03_gate.py [--ticker LUCK]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import PROJECT_ROOT, MARKDOWN_ROOT, ARTIFACTS  # noqa: E402
from shared import pricing  # noqa: E402
from shared.report import write_report, emit_decision, Action, write_stage_issues, write_stage_lineage  # noqa: E402

OUT = ARTIFACTS / "03_gate"
PAGE_COMMENT = re.compile(r"<!--\s*Page\s+\d+\s*-->", re.I)
IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
THIN, ASCII_MIN = 150, 0.85


def content_chars(md: str) -> tuple[str, int]:
    body = PAGE_COMMENT.sub("", md)
    stripped = IMG.sub("", body)
    stripped = re.sub(r"[#>*`|\s]", "", stripped)
    return body, len(stripped)


def classify(md: str, is_edge: bool) -> str | None:
    body, cc = content_chars(md)
    if is_edge:
        return "edge"
    if len(re.sub(r"\s", "", PAGE_COMMENT.sub("", md))) < 20:
        return "blank"
    if len(re.sub(r"[|\s]", "", PAGE_COMMENT.sub("", md))) < 20:
        return "empty_table"
    has_img = bool(IMG.search(body))
    if has_img and cc < 40:
        return "image_only"
    if not has_img and cc < 40:
        return "heading_only"
    if cc < THIN:
        return "thin"
    ascii_ratio = (sum(c.isascii() for c in re.sub(r"[|\s]", "", body)) /
                   max(1, len(re.sub(r"[|\s]", "", body))))
    if ascii_ratio < ASCII_MIN:
        return "urdu"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 3 — deterministic gate")
    ap.add_argument("--ticker")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    filings = sorted(p for p in MARKDOWN_ROOT.glob("*/*/*") if p.is_dir() and any(p.glob("page_*.md")))
    if a.ticker:
        filings = [f for f in filings if f.parts[-3].upper() == a.ticker.upper()]

    skip: dict[str, str] = {}
    tag_pages: list[str] = []
    lineage: list[dict] = []
    reasons: Counter = Counter()
    total = 0
    for fdir in filings:
        pages = sorted(fdir.glob("page_*.md"))
        n = len(pages)
        for i, pg in enumerate(pages):
            total += 1
            rel = str(pg.relative_to(MARKDOWN_ROOT))
            is_edge = i < 2 or i >= n - 2
            reason = classify(pg.read_text(errors="ignore"), is_edge)
            if reason:
                skip[rel] = reason
                reasons[reason] += 1
                lineage.append({"ticker": rel.split("/")[0].upper(), "relative_path": rel,
                                "filing": "/".join(rel.split("/")[:3]), "page": int("".join(c for c in pg.stem if c.isdigit())),
                                "decision": "skip", "reason": reason})
            else:
                tag_pages.append(rel)
                lineage.append({"ticker": rel.split("/")[0].upper(), "relative_path": rel,
                                "filing": "/".join(rel.split("/")[:3]), "page": int("".join(c for c in pg.stem if c.isdigit())),
                                "decision": "tag", "reason": None})

    (OUT / "skip_manifest.json").write_text(json.dumps(skip, indent=2))
    (OUT / "tag_manifest.json").write_text(json.dumps({"pages": tag_pages}, indent=2))

    pages_to_tag = len(tag_pages)
    cost = pricing.tag_cost(pages_to_tag)
    write_stage_lineage("03_gate", lineage)
    write_stage_issues("03_gate", [])
    rep = {"total_pages": total, "skipped": sum(reasons.values()),
           "by_reason": dict(reasons), "pages_to_tag": pages_to_tag,
           "est_tag_cost_usd": cost, "lineage_rows": len(lineage)}
    write_report("03_gate", rep)
    emit_decision(
        "03_gate",
        f"{total:,} pages · gated {sum(reasons.values()):,} ({dict(reasons)}) · "
        f"{pages_to_tag:,} survive → ready to tag.",
        cost_usd=cost,
        actions=[Action("Run tag", "python pipeline/04_tag.py", cost_usd=cost)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
