#!/usr/bin/env python3
"""Evaluate Page Index V2 as an additive retrieval representation.

`raw` searches trusted metadata plus original Markdown. `current` adds existing
filing search_text. `v2` adds the new search key, evidence descriptions, and
only anchors verified verbatim against Markdown.

The headline metric is page rediscovery from an independent representation:
current filing search text or a heading read directly from source Markdown.
V2 descriptions never manufacture their own headline test.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAGE_ROOT = PROJECT_ROOT / "artifacts" / "page_index_v2_pilot" / "pages"
INDEX_PATH = PROJECT_ROOT / "artifacts" / "page_index_v2_pilot" / "shadow_index.sqlite"
REPORT_PATH = PROJECT_ROOT / "artifacts" / "reports" / "page_index_v2_retrieval_eval.json"
TOP_K = 20

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
    "has", "have", "in", "is", "it", "its", "of", "on", "or", "page", "report",
    "statement", "statements", "the", "this", "to", "was", "were", "with", "year",
}

CHALLENGE_QUERIES = [
    "latest cash and cash equivalents balance HBL",
    "UBL insider purchases and sales",
    "credit risk exposure by industry sector UBL",
    "capital adequacy ratio common equity tier 1 HBL",
    "agriculture contribution to GDP and growth",
    "fertilizer production and offtake",
    "circular debt power sector",
    "policy rate inflation monetary policy",
    "dividend announcement",
    "acquisition share purchase notice",
    "marketing advertising expense",
    "installed production capacity cement",
    "deposits from customers and financial institutions",
    "foreign currency risk exposure",
    "segment revenue by product",
]


def compact(value: Any, limit: int = 8_000) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def tokens(value: Any, limit: int = 18) -> list[str]:
    found = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", str(value or "").casefold())
    result: list[str] = []
    for token in found:
        token = token.strip("./-_")
        if len(token) < 2 or token in STOP_WORDS or token in result:
            continue
        result.append(token)
        if len(result) >= limit:
            break
    return result


def first_source_heading(markdown: str) -> str:
    """Read a title-like line from source text without page-type semantics."""
    candidates: list[str] = []
    in_frontmatter = False
    for raw in markdown.splitlines()[:100]:
        line = compact(raw, 240)
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if not line or line.startswith("<!--") or line == ".":
            continue
        if line.startswith("#"):
            line = compact(line.lstrip("# "))
            if line:
                candidates.append(line)
                continue
        if line.startswith("|") or set(line) <= {"|", "-", ":", " "}:
            continue
        if 3 <= len(line.split()) <= 24 and len(line) <= 180:
            candidates.append(line.strip("*_ "))
    return candidates[0] if candidates else ""


def load_artifacts() -> list[dict[str, Any]]:
    artifacts = []
    for path in sorted(PAGE_ROOT.glob("*.json")):
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if artifact.get("status") != "ok" or not artifact.get("output"):
            continue
        source_path = PROJECT_ROOT / artifact["source_path"]
        if not source_path.is_file():
            continue
        artifact["_artifact_path"] = path.relative_to(PROJECT_ROOT).as_posix()
        artifact["_markdown"] = source_path.read_text(encoding="utf-8", errors="replace")
        artifacts.append(artifact)
    return artifacts


def metadata_text(artifact: dict[str, Any]) -> str:
    metadata = artifact.get("trusted_metadata") or {}
    return compact(" ".join(
        str(metadata.get(field) or "")
        for field in (
            "corpus", "collection", "publisher", "entity", "country",
            "document_id", "document_period", "chapter", "page_number",
        )
    ))


def v2_keys(artifact: dict[str, Any], include_current: bool = True) -> str:
    output = artifact["output"]
    current = artifact.get("current_search_text", "")
    quality = output.get("parse_quality") or {}
    blocks = output.get("evidence_blocks") or []
    if quality.get("status") == "reextract" or (
        quality.get("status") == "ok"
        and not output.get("search_text")
        and not blocks
    ):
        return compact(current if include_current else "", 16_000)
    parts = [current if include_current else "", output.get("search_text", "")]
    for block in blocks:
        parts.append(block.get("description", ""))
        parts.extend(block.get("grounded_anchors") or [])
        parts.extend(block.get("entities") or [])
        parts.extend(block.get("periods") or [])
        parts.extend(block.get("qualifiers") or [])
        parts.extend(block.get("units") or [])
    return compact(" ".join(str(part or "") for part in parts), 16_000)


def create_index(artifacts: list[dict[str, Any]]) -> sqlite3.Connection:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(INDEX_PATH)
    db.executescript(
        """
        DROP TABLE IF EXISTS docs;
        DROP TABLE IF EXISTS raw_fts;
        DROP TABLE IF EXISTS current_fts;
        DROP TABLE IF EXISTS v2_fts;
        DROP TABLE IF EXISTS signals_fts;
        CREATE TABLE docs (
          id INTEGER PRIMARY KEY,
          source_path TEXT NOT NULL UNIQUE,
          cohort TEXT NOT NULL,
          corpus TEXT NOT NULL,
          entity TEXT NOT NULL,
          document_id TEXT NOT NULL,
          artifact_path TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE raw_fts USING fts5(metadata, keys, content);
        CREATE VIRTUAL TABLE current_fts USING fts5(metadata, keys, content);
        CREATE VIRTUAL TABLE v2_fts USING fts5(metadata, keys, content);
        CREATE VIRTUAL TABLE signals_fts USING fts5(metadata, keys, content);
        """
    )
    for rowid, artifact in enumerate(artifacts, start=1):
        metadata = artifact.get("trusted_metadata") or {}
        db.execute(
            "INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rowid,
                artifact["source_path"],
                artifact["cohort"],
                str(metadata.get("corpus") or ""),
                str(metadata.get("entity") or ""),
                str(metadata.get("document_id") or ""),
                artifact["_artifact_path"],
            ),
        )
        raw = artifact["_markdown"]
        meta = metadata_text(artifact)
        current = compact(artifact.get("current_search_text"), 8_000)
        db.execute("INSERT INTO raw_fts(rowid, metadata, keys, content) VALUES (?, ?, ?, ?)",
                   (rowid, meta, "", raw))
        db.execute("INSERT INTO current_fts(rowid, metadata, keys, content) VALUES (?, ?, ?, ?)",
                   (rowid, meta, current, raw))
        db.execute("INSERT INTO v2_fts(rowid, metadata, keys, content) VALUES (?, ?, ?, ?)",
                   (rowid, meta, v2_keys(artifact), raw))
        db.execute("INSERT INTO signals_fts(rowid, metadata, keys, content) VALUES (?, ?, ?, ?)",
                   (rowid, meta, v2_keys(artifact, include_current=False), ""))
    db.commit()
    return db


def search(
    db: sqlite3.Connection,
    variant: str,
    query: str,
    corpus: str = "",
    entity: str = "",
    document_id: str = "",
    limit: int = TOP_K,
    key_weight: float = 6.0,
) -> list[dict[str, Any]]:
    query_tokens = tokens(query)
    if not query_tokens:
        return []
    match = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in query_tokens)
    filters = []
    params: list[Any] = [match]
    if corpus:
        filters.append("d.corpus = ?")
        params.append(corpus)
    if entity:
        filters.append("d.entity = ?")
        params.append(entity)
    if document_id:
        filters.append("d.document_id = ?")
        params.append(document_id)
    where = f" AND {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    rows = db.execute(
        f"""
        SELECT d.source_path, d.cohort, d.corpus, d.entity, d.artifact_path,
               bm25({variant}_fts, 3.0, {float(key_weight)}, 0.6) AS score
        FROM {variant}_fts
        JOIN docs d ON d.id = {variant}_fts.rowid
        WHERE {variant}_fts MATCH ? {where}
        ORDER BY score
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        {
            "source_path": row[0],
            "cohort": row[1],
            "corpus": row[2],
            "entity": row[3],
            "artifact_path": row[4],
            "score": round(float(row[5]), 6),
        }
        for row in rows
    ]


def reciprocal_rank(results: list[dict[str, Any]], target: str) -> float:
    for rank, result in enumerate(results, start=1):
        if result["source_path"] == target:
            return 1 / rank
    return 0.0


def search_rrf(
    db: sqlite3.Connection,
    query: str,
    corpus: str = "",
    entity: str = "",
    document_id: str = "",
    limit: int = TOP_K,
    rrf_constant: int = 60,
) -> list[dict[str, Any]]:
    """Fuse current K/V retrieval with the independent V2 key lane."""
    lanes = {
        "current": search(
            db, "current", query, corpus, entity, document_id, limit=100,
        ),
        "signals": search(
            db, "signals", query, corpus, entity, document_id, limit=100,
        ),
    }
    fused: dict[str, dict[str, Any]] = {}
    for lane, results in lanes.items():
        for rank, result in enumerate(results, start=1):
            path = result["source_path"]
            item = fused.setdefault(path, {
                **result,
                "score": 0.0,
                "lane_ranks": {},
            })
            item["score"] += 1 / (rrf_constant + rank)
            item["lane_ranks"][lane] = rank
    return sorted(
        fused.values(),
        key=lambda item: (-item["score"], item["source_path"]),
    )[:limit]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.floor(len(ordered) * fraction))]


def build_rediscovery_cases(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    for artifact in artifacts:
        metadata = artifact.get("trusted_metadata") or {}
        current = compact(artifact.get("current_search_text"), 1_200)
        source_heading = first_source_heading(artifact["_markdown"])
        if current and len(tokens(current)) >= 3:
            query = current
            source = "current_search_text"
        elif source_heading and len(tokens(source_heading)) >= 2:
            query = source_heading
            source = "source_heading"
        else:
            continue
        cases.append({
            "target": artifact["source_path"],
            "cohort": artifact["cohort"],
            "corpus": str(metadata.get("corpus") or ""),
            "entity": str(metadata.get("entity") or ""),
            "document_id": str(metadata.get("document_id") or ""),
            "query": query,
            "query_source": source,
        })
    return cases


def evaluate_variant(
    db: sqlite3.Connection,
    variant: str,
    cases: list[dict[str, Any]],
    key_weight: float = 6.0,
) -> tuple[dict[str, Any], dict[str, float]]:
    reciprocal_ranks = []
    rank_by_target = {}
    recalls = Counter()
    by_cohort: dict[str, list[float]] = defaultdict(list)
    by_query_source: dict[str, list[float]] = defaultdict(list)
    for case in cases:
        document_scope = (
            case["document_id"]
            if case["query_source"] == "source_heading"
            and case["cohort"] in {"authoritative", "economic_survey", "macro_other"}
            else ""
        )
        if variant == "rrf":
            results = search_rrf(
                db,
                case["query"],
                corpus=case["corpus"],
                entity=case["entity"],
                document_id=document_scope,
                limit=TOP_K,
            )
        else:
            results = search(
                db,
                variant,
                case["query"],
                corpus=case["corpus"],
                entity=case["entity"],
                document_id=document_scope,
                limit=TOP_K,
                key_weight=key_weight,
            )
        rr = reciprocal_rank(results, case["target"])
        reciprocal_ranks.append(rr)
        rank_by_target[case["target"]] = rr
        by_cohort[case["cohort"]].append(rr)
        by_query_source[case["query_source"]].append(rr)
        if rr:
            rank = round(1 / rr)
            for cutoff in (1, 3, 5, 10, 20):
                recalls[cutoff] += rank <= cutoff

    count = len(cases)
    metrics = {
        "cases": count,
        "mrr_at_20": round(sum(reciprocal_ranks) / count, 4) if count else None,
        **{
            f"recall_at_{cutoff}": round(recalls[cutoff] / count, 4) if count else None
            for cutoff in (1, 3, 5, 10, 20)
        },
        "by_cohort": {
            cohort: {
                "cases": len(values),
                "mrr_at_20": round(sum(values) / len(values), 4),
                "found_at_20": round(sum(value > 0 for value in values) / len(values), 4),
            }
            for cohort, values in sorted(by_cohort.items())
        },
        "by_query_source": {
            source: {
                "cases": len(values),
                "mrr_at_20": round(sum(values) / len(values), 4),
                "found_at_20": round(sum(value > 0 for value in values) / len(values), 4),
            }
            for source, values in sorted(by_query_source.items())
        },
    }
    return metrics, rank_by_target


def quality_audit(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    contradictions = []
    ungrounded_pages = []
    block_shapes = Counter()
    evidence_counts = []
    gated_from_v2 = []
    for artifact in artifacts:
        output = artifact["output"]
        status = output.get("parse_quality", {}).get("status")
        has_search = bool(output.get("search_text"))
        blocks = output.get("evidence_blocks") or []
        evidence_counts.append(len(blocks))
        if status == "ok" and not has_search and not blocks:
            contradictions.append({
                "source_path": artifact["source_path"],
                "artifact_path": artifact["_artifact_path"],
                "input_chars": artifact.get("input_chars"),
            })
        if status == "reextract" or (status == "ok" and not has_search and not blocks):
            gated_from_v2.append(artifact["source_path"])
        metrics = artifact.get("validation_metrics") or {}
        if metrics.get("anchors", 0) > metrics.get("grounded_anchors", 0):
            ungrounded_pages.append(artifact["source_path"])
        for block in blocks:
            block_shapes[str(block.get("data_shape") or "none")] += 1
    return {
        "ok_but_empty_signal_count": len(contradictions),
        "ok_but_empty_signal_pages": contradictions,
        "pages_gated_from_v2_signals": len(gated_from_v2),
        "pages_eligible_for_v2_signals": len(artifacts) - len(gated_from_v2),
        "pages_with_any_ungrounded_anchor": len(ungrounded_pages),
        "evidence_blocks_per_page": {
            "mean": round(sum(evidence_counts) / len(evidence_counts), 3),
            "median": percentile(evidence_counts, 0.5),
            "p90": percentile(evidence_counts, 0.9),
        },
        "data_shapes": dict(sorted(block_shapes.items())),
    }


def challenge_results(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = []
    for query in CHALLENGE_QUERIES:
        result = {"query": query}
        for variant in ("raw", "current", "v2"):
            result[variant] = search(db, variant, query, limit=5)
        result["rrf"] = search_rrf(db, query, limit=5)
        rows.append(result)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    artifacts = load_artifacts()
    if not artifacts:
        raise SystemExit(f"No completed artifacts under {PAGE_ROOT}")
    db = create_index(artifacts)
    cases = build_rediscovery_cases(artifacts)
    metrics = {}
    ranks = {}
    for variant in ("raw", "current", "v2", "rrf"):
        metrics[variant], ranks[variant] = evaluate_variant(db, variant, cases)
    v2_weight_sweep = {}
    for key_weight in (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0):
        sweep_metrics, _ = evaluate_variant(db, "v2", cases, key_weight=key_weight)
        v2_weight_sweep[str(key_weight)] = sweep_metrics

    movements = []
    for case in cases:
        target = case["target"]
        current_rr = ranks["current"].get(target, 0)
        rrf_rr = ranks["rrf"].get(target, 0)
        if current_rr == rrf_rr:
            continue
        movements.append({
            **case,
            "current_rank": round(1 / current_rr) if current_rr else None,
            "rrf_rank": round(1 / rrf_rr) if rrf_rr else None,
            "delta_rr": round(rrf_rr - current_rr, 6),
        })
    improved = sorted(
        (row for row in movements if row["delta_rr"] > 0),
        key=lambda row: row["delta_rr"],
        reverse=True,
    )
    worsened = sorted(
        (row for row in movements if row["delta_rr"] < 0),
        key=lambda row: row["delta_rr"],
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": {
            "description": (
                "Shadow BM25/FTS5 ablation over the same 1,000 pages. V2 is additive. "
                "Rediscovery queries come from current filing search_text or a source "
                "Markdown heading, never from V2 descriptions."
            ),
            "index_variants": {
                "raw": "trusted metadata + original Markdown",
                "current": "raw + existing filing search_text",
                "v2": "current + V2 search_text + evidence descriptions + grounded anchors",
                "rrf": "rank fusion of current K/V retrieval and an independent V2 key lane",
            },
            "bm25_weights": {"metadata": 3.0, "keys": 6.0, "content": 0.6},
            "scope": "same corpus; same entity when the trusted entity is present",
            "top_k": TOP_K,
        },
        "artifacts": len(artifacts),
        "shadow_index": str(INDEX_PATH.relative_to(PROJECT_ROOT)),
        "rediscovery_cases": {
            "count": len(cases),
            "query_sources": dict(sorted(Counter(case["query_source"] for case in cases).items())),
            "by_cohort": dict(sorted(Counter(case["cohort"] for case in cases).items())),
        },
        "metrics": metrics,
        "v2_key_weight_sweep": v2_weight_sweep,
        "movements": {
            "improved_count": len(improved),
            "worsened_count": len(worsened),
            "unchanged_count": len(cases) - len(improved) - len(worsened),
            "largest_improvements": improved[:20],
            "largest_regressions": worsened[:20],
        },
        "quality_audit": quality_audit(artifacts),
        "challenge_queries": challenge_results(db),
        "limitations": [
            "Rediscovery measures whether a known page can be found, not whether a multi-page answer is complete.",
            "The 1,000-page stratified sample is a shadow corpus, not the full production corpus.",
            "Challenge-query results are included for inspection and are not scored without independent page-level qrels.",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(args.report.relative_to(PROJECT_ROOT)),
        "artifacts": len(artifacts),
        "rediscovery_cases": len(cases),
        "metrics": metrics,
        "movements": {
            "improved_count": len(improved),
            "worsened_count": len(worsened),
            "unchanged_count": len(cases) - len(improved) - len(worsened),
        },
        "quality_audit": report["quality_audit"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
