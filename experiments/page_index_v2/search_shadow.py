#!/usr/bin/env python3
"""Search the Page Index V2 shadow corpus and inspect its retrieval keys."""
from __future__ import annotations

import argparse
import json
import sqlite3

from evaluate import INDEX_PATH, PROJECT_ROOT, search, search_rrf


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--variant", choices=("raw", "current", "v2", "rrf"), default="rrf")
    parser.add_argument("--corpus", default="")
    parser.add_argument("--entity", default="")
    parser.add_argument("--document-id", default="")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    if not INDEX_PATH.is_file():
        raise SystemExit("Run experiments/page_index_v2/evaluate.py first")
    db = sqlite3.connect(INDEX_PATH)
    if args.variant == "rrf":
        results = search_rrf(
            db, args.query, args.corpus, args.entity, args.document_id, args.limit,
        )
    else:
        results = search(
            db, args.variant, args.query, args.corpus, args.entity,
            args.document_id, args.limit,
        )

    visible = []
    for rank, result in enumerate(results, start=1):
        artifact = json.loads(
            (PROJECT_ROOT / result["artifact_path"]).read_text(encoding="utf-8")
        )
        output = artifact.get("output") or {}
        visible.append({
            "rank": rank,
            "source_path": result["source_path"],
            "cohort": result["cohort"],
            "entity": result["entity"],
            "score": result["score"],
            "lane_ranks": result.get("lane_ranks"),
            "search_text": output.get("search_text"),
            "evidence": [
                block.get("description")
                for block in (output.get("evidence_blocks") or [])[:4]
            ],
        })
    print(json.dumps(visible, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
