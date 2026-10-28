#!/usr/bin/env python3
"""Evaluate compact keys on 50 routed, user-shaped retrieval cases."""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "page_index_v3_20k"
PAGE_ROOT = OUTPUT_ROOT / "pages"
SAMPLE_PATH = OUTPUT_ROOT / "sample.jsonl"
INDEX_PATH = OUTPUT_ROOT / "shadow_index.sqlite"
JUDGE_ROOT = OUTPUT_ROOT / "judge"
REPORT_PATH = PROJECT_ROOT / "artifacts" / "reports" / "page_index_v3_retrieval_eval.json"
MODEL = "deepseek-v4-flash"
JUDGE_VERSION = "2026-07-29.2"
PRICE_PER_MILLION = {"input": 0.14, "output": 0.28}
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "how", "in", "is", "it", "of", "on", "or", "show", "the", "this",
    "to", "was", "were", "what", "which", "with", "year", "years",
}


def compact(value: Any, limit: int = 5_000) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def tokens(value: Any, limit: int = 28) -> list[str]:
    found = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", str(value or "").casefold())
    result = []
    for token in found:
        token = token.strip("./-_")
        if len(token) < 2 or token in STOP_WORDS or token in result:
            continue
        result.append(token)
        if len(result) >= limit:
            break
    return result


def load_cases() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["node", "experiments/page_index_v3/export_eval_cases.mjs"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_rows() -> list[dict[str, Any]]:
    sample = [
        json.loads(line)
        for line in SAMPLE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = []
    for item in sample:
        artifact_path = PAGE_ROOT / f"{item['artifact_id']}.json"
        if not artifact_path.is_file():
            continue
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        markdown = (PROJECT_ROOT / item["source_path"]).read_text(
            encoding="utf-8", errors="replace"
        )
        rows.append({
            **item,
            **artifact,
            "output": artifact.get("output") or {},
            "_artifact_path": artifact_path.relative_to(PROJECT_ROOT).as_posix(),
            "_markdown": markdown,
        })
    return rows


def metadata_text(row: dict[str, Any]) -> str:
    metadata = row.get("trusted_metadata") or {}
    return compact(" ".join(
        str(metadata.get(field) or "")
        for field in (
            "corpus", "collection", "publisher", "entity", "country",
            "document_id", "document_period", "chapter", "page_number",
        )
    ))


def build_index(rows: list[dict[str, Any]]) -> sqlite3.Connection:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(INDEX_PATH)
    db.executescript(
        """
        DROP TABLE IF EXISTS docs;
        DROP TABLE IF EXISTS baseline_fts;
        DROP TABLE IF EXISTS compact_fts;
        DROP TABLE IF EXISTS key_fts;
        CREATE TABLE docs (
          id INTEGER PRIMARY KEY,
          source_path TEXT NOT NULL UNIQUE,
          cohort TEXT NOT NULL,
          corpus TEXT NOT NULL,
          entity TEXT NOT NULL,
          collection TEXT NOT NULL,
          document_period TEXT NOT NULL,
          document_id TEXT NOT NULL,
          artifact_path TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE baseline_fts USING fts5(metadata, current_key, content);
        CREATE VIRTUAL TABLE compact_fts USING fts5(
          metadata, current_key, compact_key, phrases, content
        );
        CREATE VIRTUAL TABLE key_fts USING fts5(metadata, compact_key, phrases);
        """
    )
    for rowid, row in enumerate(rows, start=1):
        metadata = row.get("trusted_metadata") or {}
        output = row.get("output") or {}
        eligible = bool(output.get("eligible_for_key_index"))
        key = output.get("retrieval_key", "") if eligible else ""
        phrases = " ".join(output.get("retrieval_phrases") or []) if eligible else ""
        db.execute(
            "INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rowid,
                row["source_path"],
                row["cohort"],
                str(metadata.get("corpus") or ""),
                str(metadata.get("entity") or ""),
                str(metadata.get("collection") or ""),
                str(metadata.get("document_period") or ""),
                str(metadata.get("document_id") or ""),
                row["_artifact_path"],
            ),
        )
        meta = metadata_text(row)
        current = row.get("current_search_text") or ""
        raw = row["_markdown"]
        db.execute(
            "INSERT INTO baseline_fts(rowid, metadata, current_key, content) VALUES (?, ?, ?, ?)",
            (rowid, meta, current, raw),
        )
        db.execute(
            "INSERT INTO compact_fts(rowid, metadata, current_key, compact_key, phrases, content) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rowid, meta, current, key, phrases, raw),
        )
        db.execute(
            "INSERT INTO key_fts(rowid, metadata, compact_key, phrases) VALUES (?, ?, ?, ?)",
            (rowid, meta, key, phrases),
        )
    db.commit()
    return db


def filters_for_case(case: dict[str, Any]) -> tuple[list[str], list[Any]]:
    params = case.get("params") or {}
    clauses = []
    values: list[Any] = []
    mapping = (
        ("corpora", "d.corpus"),
        ("tickers", "d.entity"),
        ("years", "d.document_period"),
    )
    for param_name, column in mapping:
        requested = [
            item.strip()
            for item in str(params.get(param_name) or "").split(",")
            if item.strip()
        ]
        if not requested:
            continue
        placeholders = ",".join("?" for _ in requested)
        clauses.append(f"{column} IN ({placeholders})")
        values.extend(requested)
    cadence = str(params.get("filing_cadence") or "").strip()
    if cadence in {"annual", "quarterly"}:
        clauses.append("(d.corpus <> 'filing' OR d.collection = ?)")
        values.append(cadence)
    return clauses, values


def search(
    db: sqlite3.Connection,
    variant: str,
    query: str,
    case: dict[str, Any],
    limit: int = 20,
) -> list[dict[str, Any]]:
    query_tokens = tokens(query)
    if not query_tokens:
        return []
    expression = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in query_tokens)
    clauses, values = filters_for_case(case)
    extra = f" AND {' AND '.join(clauses)}" if clauses else ""
    weights = {
        "baseline": "3.0, 6.0, 0.5",
        # Preserve every baseline weight. This variant must differ only by
        # adding the generated key and analyst-phrase fields.
        "compact": "3.0, 6.0, 8.0, 5.0, 0.5",
        "key": "3.0, 8.0, 5.0",
    }[variant]
    rows = db.execute(
        f"""
        SELECT d.source_path, d.cohort, d.corpus, d.entity, d.collection,
               d.document_period, d.document_id, d.artifact_path,
               bm25({variant}_fts, {weights}) AS score
        FROM {variant}_fts
        JOIN docs d ON d.id = {variant}_fts.rowid
        WHERE {variant}_fts MATCH ? {extra}
        ORDER BY score
        LIMIT ?
        """,
        [expression, *values, limit],
    ).fetchall()
    return [
        {
            "source_path": row[0],
            "cohort": row[1],
            "corpus": row[2],
            "entity": row[3],
            "collection": row[4],
            "document_period": row[5],
            "document_id": row[6],
            "artifact_path": row[7],
            "score": round(float(row[8]), 6),
        }
        for row in rows
    ]


def routed_query(case: dict[str, Any]) -> str:
    routed = case.get("routed") or {}
    return " ".join(dict.fromkeys(
        compact(routed.get(field), 300)
        for field in ("q", "q_summary", "q_text", "q_heading")
        if compact(routed.get(field), 300)
    ))


def rrf(
    baseline: list[dict[str, Any]],
    key: list[dict[str, Any]],
    limit: int = 5,
    constant: int = 60,
) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for lane, results in (("baseline", baseline), ("key", key)):
        for rank, result in enumerate(results, start=1):
            item = fused.setdefault(result["source_path"], {
                **result,
                "score": 0.0,
                "lane_ranks": {},
            })
            item["score"] += 1 / (constant + rank)
            item["lane_ranks"][lane] = rank
    return sorted(
        fused.values(),
        key=lambda item: (-item["score"], item["source_path"]),
    )[:limit]


def candidate_payload(
    candidates: list[dict[str, Any]],
    seed: str,
    query: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    unique = {candidate["source_path"]: candidate for candidate in candidates}
    ordered = list(unique.values())
    random.Random(seed).shuffle(ordered)
    visible = []
    mapping = {}
    for index, candidate in enumerate(ordered, start=1):
        candidate_id = f"C{index:02d}"
        artifact = json.loads(
            (PROJECT_ROOT / candidate["artifact_path"]).read_text(encoding="utf-8")
        )
        markdown = (PROJECT_ROOT / candidate["source_path"]).read_text(
            encoding="utf-8", errors="replace"
        )
        visible.append({
            "candidate_id": candidate_id,
            "corpus": candidate["corpus"],
            "entity": candidate["entity"] or None,
            "document": candidate["document_id"],
            "period": candidate["document_period"] or None,
            "source_excerpt": evidence_excerpt(markdown, query),
        })
        mapping[candidate_id] = {
            **candidate,
            "display_summary": (artifact.get("output") or {}).get("display_summary"),
        }
    return visible, mapping


def evidence_excerpt(markdown: str, query: str, limit: int = 6_000) -> str:
    """Show the judge source evidence, not generated fields, without head-only bias."""
    if len(markdown) <= limit:
        return markdown
    lowered = markdown.casefold()
    windows: list[tuple[int, int]] = [(0, min(1_200, len(markdown)))]
    for term in tokens(query, limit=24):
        start = 0
        for _ in range(2):
            found = lowered.find(term.casefold(), start)
            if found < 0:
                break
            windows.append((max(0, found - 450), min(len(markdown), found + 950)))
            start = found + len(term)
    windows.sort()
    merged: list[tuple[int, int]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1] + 120:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    parts = []
    used = 0
    for start, end in merged:
        remaining = limit - used
        if remaining <= 0:
            break
        chunk = markdown[start:end][:remaining]
        if chunk:
            parts.append(chunk)
            used += len(chunk)
    return "\n\n[...]\n\n".join(parts)[:limit]


def judge_case(
    client: OpenAI,
    case: dict[str, Any],
    visible: list[dict[str, Any]],
    mapping: dict[str, dict[str, Any]],
    reset: bool,
) -> dict[str, Any]:
    JUDGE_ROOT.mkdir(parents=True, exist_ok=True)
    path = JUDGE_ROOT / f"{case['id']}.json"
    if path.is_file() and not reset:
        cached = json.loads(path.read_text(encoding="utf-8"))
        cached_paths = {
            item.get("source_path")
            for item in (cached.get("blind_candidate_map") or {}).values()
        }
        expected_paths = {item["source_path"] for item in mapping.values()}
        if cached.get("judge_version") == JUDGE_VERSION and cached_paths == expected_paths:
            return cached
    prompt = f"""
Judge source-page relevance for an analyst retrieval query.

QUERY:
{case['natural_query']}

EXPECTED SEARCH SCOPE:
{json.dumps(case.get('params') or {}, ensure_ascii=False)}

Score every candidate using only its original source excerpt:
0 = irrelevant or wrong entity/corpus
1 = topically related but not useful evidence for the query
2 = useful partial evidence
3 = directly answer-bearing evidence

A cover, contents page, generic boilerplate, or document mention is not
answer-bearing unless the query explicitly asks for navigation. Do not reward
keyword overlap without the requested relationship or measurement.

Return strict JSON:
{{"judgments":[{{"candidate_id":"C01","score":0,"reason":""}}]}}

CANDIDATES:
{json.dumps(visible, ensure_ascii=False)}
"""
    started = time.perf_counter()
    cumulative_input = 0
    cumulative_output = 0
    cumulative_cost = 0.0
    last_error = ""
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=0,
                max_tokens=2_400,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Judge retrieval relevance from source evidence only."},
                    {"role": "user", "content": prompt},
                ],
            )
            usage = response.usage
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            cumulative_input += input_tokens
            cumulative_output += output_tokens
            cumulative_cost += (
                input_tokens * PRICE_PER_MILLION["input"]
                + output_tokens * PRICE_PER_MILLION["output"]
            ) / 1_000_000
            raw = json.loads(response.choices[0].message.content or "")
            scores = {}
            reasons = {}
            for judgment in raw.get("judgments") or []:
                candidate_id = str(judgment.get("candidate_id") or "")
                if candidate_id not in mapping:
                    continue
                score = max(0, min(3, int(judgment.get("score") or 0)))
                path_key = mapping[candidate_id]["source_path"]
                scores[path_key] = score
                reasons[path_key] = compact(judgment.get("reason"), 300)
            expected_paths = {
                candidate["source_path"] for candidate in mapping.values()
            }
            if set(scores) != expected_paths:
                raise ValueError(
                    f"judge returned {len(scores)} of {len(expected_paths)} candidates"
                )
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            if attempt == 3:
                raise RuntimeError(last_error) from exc
            time.sleep(attempt * 2)
    artifact = {
        "case_id": case["id"],
        "query": case["natural_query"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "judge_version": JUDGE_VERSION,
        "blind_candidate_map": mapping,
        "scores": scores,
        "reasons": reasons,
        "usage": {
            "input_tokens": cumulative_input,
            "output_tokens": cumulative_output,
            "cost_usd": round(cumulative_cost, 8),
        },
        "attempts": attempt,
        "latency_ms": round((time.perf_counter() - started) * 1_000),
        "raw_response": raw,
    }
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def dcg(scores: list[int]) -> float:
    return sum(score / math.log2(rank + 1) for rank, score in enumerate(scores, start=1))


def variant_metrics(
    cases: list[dict[str, Any]],
    case_results: dict[str, dict[str, list[dict[str, Any]]]],
    judgments: dict[str, dict[str, Any]],
    variant: str,
) -> dict[str, Any]:
    per_case = []
    for case in cases:
        results = case_results[case["id"]][variant][:5]
        score_map = judgments[case["id"]]["scores"]
        scores = [int(score_map.get(result["source_path"], 0)) for result in results]
        ideal = sorted(judgments[case["id"]]["scores"].values(), reverse=True)[:5]
        ideal_dcg = dcg([int(value) for value in ideal])
        per_case.append({
            "case_id": case["id"],
            "scores": scores,
            "precision_answer_bearing": sum(score >= 2 for score in scores) / 5,
            "first_answer_bearing": bool(scores and scores[0] >= 2),
            "ndcg_at_5": dcg(scores) / ideal_dcg if ideal_dcg else 0,
        })
    return {
        "cases": len(per_case),
        "mean_relevance_at_5": round(
            sum(sum(row["scores"]) / 5 for row in per_case) / len(per_case), 4
        ),
        "precision_answer_bearing_at_5": round(
            sum(row["precision_answer_bearing"] for row in per_case) / len(per_case), 4
        ),
        "first_result_answer_bearing_rate": round(
            sum(row["first_answer_bearing"] for row in per_case) / len(per_case), 4
        ),
        "mean_ndcg_at_5": round(
            sum(row["ndcg_at_5"] for row in per_case) / len(per_case), 4
        ),
        "per_case": per_case,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge-budget-usd", type=float, default=1.5)
    parser.add_argument("--reset-judge", action="store_true")
    args = parser.parse_args()

    cases = load_cases()
    rows = load_rows()
    if len(rows) != 20_000:
        raise SystemExit(f"Expected the exact 20,000-page corpus; found {len(rows):,}")
    db = build_index(rows)
    case_results = {}
    judge_inputs = {}
    for case in cases:
        query = routed_query(case)
        baseline = search(db, "baseline", query, case, limit=20)
        compact_results = search(db, "compact", query, case, limit=20)
        key_results = search(db, "key", query, case, limit=20)
        fused = rrf(baseline, key_results, limit=20)
        case_results[case["id"]] = {
            "baseline": baseline,
            "compact": compact_results,
            "key": key_results,
            "rrf": fused,
        }
        candidates = baseline[:5] + compact_results[:5] + key_results[:5] + fused[:5]
        judge_inputs[case["id"]] = candidate_payload(
            candidates,
            case["id"],
            f"{case['natural_query']} {query}",
        )

    load_dotenv(PROJECT_ROOT / ".env")
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
        timeout=120,
        max_retries=1,
    )
    judgments = {}
    judge_spend = 0.0
    for index, case in enumerate(cases, start=1):
        visible, mapping = judge_inputs[case["id"]]
        if judge_spend >= args.judge_budget_usd:
            raise RuntimeError("Judge budget exhausted before all cases completed")
        artifact = judge_case(client, case, visible, mapping, args.reset_judge)
        judgments[case["id"]] = artifact
        judge_spend += float(artifact.get("usage", {}).get("cost_usd", 0))
        print(f"[{index}/{len(cases)}] {case['id']} judge spend ${judge_spend:.4f}")

    unavailable_cases = [
        case
        for case in cases
        if not any(case_results[case["id"]][variant] for variant in ("baseline", "compact", "key", "rrf"))
    ]
    evaluable_cases = [
        case for case in cases if case["id"] not in {item["id"] for item in unavailable_cases}
    ]
    metrics = {
        variant: variant_metrics(evaluable_cases, case_results, judgments, variant)
        for variant in ("baseline", "compact", "key", "rrf")
    }
    all_case_metrics = {
        variant: variant_metrics(cases, case_results, judgments, variant)
        for variant in ("baseline", "compact", "key", "rrf")
    }
    enrichment_report = json.loads(
        (
            PROJECT_ROOT / "artifacts" / "reports" / "page_index_v3_enrichment.json"
        ).read_text(encoding="utf-8")
    )
    generation_cost = float(
        enrichment_report.get("usage", {}).get("cost_usd", 0)
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_judge": MODEL,
        "design": {
            "cases": "50 paraphrases across 14 families modeled on production D1 query logs",
            "evaluable_cases": len(evaluable_cases),
            "scope_unavailable_cases": [
                {
                    "id": case["id"],
                    "query": case["natural_query"],
                    "filters": case.get("params") or {},
                }
                for case in unavailable_cases
            ],
            "scope": "gold corpus/entity/cadence filters from the existing retrieval eval",
            "query": "same hand-authored gold routed query for every variant; live router is not evaluated",
            "ablation": "baseline column weights are held constant; compact adds only generated key and phrase fields",
            "blind_judging": "candidate source excerpts only; generated keys, lane, and rank hidden",
            "score_scale": {
                "0": "irrelevant or wrong scope",
                "1": "topical but not useful evidence",
                "2": "useful partial evidence",
                "3": "directly answer-bearing",
            },
        },
        "records_indexed": len(rows),
        "records_enriched": sum(row.get("status") == "ok" for row in rows),
        "cases": len(cases),
        "metrics": metrics,
        "all_case_metrics_including_unavailable_scope": all_case_metrics,
        "judge_usage": {
            "cost_usd": round(judge_spend, 6),
            "budget_usd": args.judge_budget_usd,
            "input_tokens": sum(
                int(row.get("usage", {}).get("input_tokens", 0))
                for row in judgments.values()
            ),
            "output_tokens": sum(
                int(row.get("usage", {}).get("output_tokens", 0))
                for row in judgments.values()
            ),
        },
        "experiment_spend": {
            "generation_cost_usd": round(generation_cost, 6),
            "judge_cost_usd": round(judge_spend, 6),
            "combined_cost_usd": round(generation_cost + judge_spend, 6),
            "user_cap_usd": 20.0,
        },
        "case_results": {
            case["id"]: {
                "query": case["natural_query"],
                "family": case["family"],
                "results": {
                    variant: [
                        {
                            **result,
                            "judge_score": judgments[case["id"]]["scores"].get(
                                result["source_path"], 0
                            ),
                            "judge_reason": judgments[case["id"]]["reasons"].get(
                                result["source_path"], ""
                            ),
                        }
                        for result in case_results[case["id"]][variant][:5]
                    ]
                    for variant in ("baseline", "compact", "key", "rrf")
                },
            }
            for case in cases
        },
        "limitations": [
            "Relevance labels are blind model judgments, not human qrels.",
            "Industry filters are not applied because industry was not added to this experiment manifest.",
            "The 20,000-record corpus intentionally contains all non-filing records but only query-centred recent filing candidates.",
            "Four inherited 2024 annual-filing cases have no matching period in the deliberately latest-period filing corpus and are reported as scope unavailable rather than scored as retrieval failures.",
        ],
        "shadow_index": str(INDEX_PATH.relative_to(PROJECT_ROOT)),
        "judge_root": str(JUDGE_ROOT.relative_to(PROJECT_ROOT)),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(REPORT_PATH.relative_to(PROJECT_ROOT)),
        "records_indexed": len(rows),
        "metrics": {
            variant: {
                key: value
                for key, value in metrics[variant].items()
                if key != "per_case"
            }
            for variant in metrics
        },
        "judge_usage": report["judge_usage"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
