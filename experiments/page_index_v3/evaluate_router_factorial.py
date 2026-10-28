#!/usr/bin/env python3
"""Run a controlled router x page-representation retrieval experiment.

The candidate universe is the exact 20,000 records in page_index_v3_20k.
Every query is routed once by each frozen router. Each persisted route is then
searched against both the baseline and enriched FTS representations.

Intermediate artifacts are deliberately first-class:
  routes/<case>.json       raw old/new plans and latency
  retrieval/<case>.json   facet searches and final rankings for all four cells
  judgments/<case>.json   blind source-page relevance judgments
  cases/<case>.json       scored cells and per-case attribution
  eval_report.{json,md}    aggregate metrics and movement audit
"""
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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = PROJECT_ROOT / "artifacts" / "page_index_v3_20k" / "shadow_index.sqlite"
RUNS_ROOT = PROJECT_ROOT / "artifacts" / "page_index_v3_factorial" / "runs"
REFERENCE_YEAR = 2026
JUDGE_MODEL = "gemini-3.5-flash-lite"
JUDGE_PROMPT_VERSION = "page-index-factorial-judge.v1"
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "compare", "do", "does",
    "for", "from", "get", "give", "has", "have", "how", "in", "include", "is",
    "it", "latest", "me", "of", "on", "or", "over", "past", "recent", "review",
    "show", "the", "these", "this", "to", "versus", "was", "were", "what",
    "where", "which", "with", "year", "years",
}
CELL_NAMES = (
    "old_router_old_representation",
    "old_router_new_representation",
    "new_router_old_representation",
    "new_router_new_representation",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact(value: Any, limit: int = 2_000) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_cases() -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            "node",
            "apps/corpus-browser/scripts/eval-retrieval-50.mjs",
            "--export-cases",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def request_json(url: str, body: dict[str, Any], timeout: float = 20) -> tuple[int, Any]:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer corpus-router-eval",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        return error.code, payload
    except (URLError, TimeoutError) as error:
        return 599, {"error": str(error)}


def route_case(base_url: str, case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    status, payload = request_json(
        f"{base_url.rstrip('/')}/api/route",
        {"query": case["natural_query"], "clientYear": REFERENCE_YEAR},
        timeout=30,
    )
    return {
        "http_status": status,
        "latency_ms": round((time.perf_counter() - started) * 1_000),
        "plan": payload if status == 200 else None,
        "error": None if status == 200 else payload,
    }


def tokens(value: Any, limit: int = 36) -> list[str]:
    found = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", str(value or "").casefold())
    output: list[str] = []
    for token in found:
        token = token.strip("./-_")
        if len(token) < 2 or token in STOP_WORDS or token in output:
            continue
        output.append(token)
        if len(output) >= limit:
            break
    return output


def fts_expression(value: str) -> str:
    terms = tokens(value)
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)


def normalized_corpora(route: dict[str, Any]) -> list[str]:
    values = []
    for corpus in route.get("corpora") or []:
        corpus = compact(corpus, 30).casefold()
        if corpus in {"filing", "announcement"}:
            values.append(corpus)
        elif corpus == "macro":
            values.extend(["macro", "authoritative"])
    return list(dict.fromkeys(values))


def route_units(route: dict[str, Any]) -> list[dict[str, Any]]:
    facets = route.get("searchFacets") or []
    if facets:
        units = []
        for index, facet in enumerate(facets[:4], start=1):
            fields = facet.get("fieldQueries") or {}
            units.append({
                "label": compact(facet.get("label"), 80) or f"Evidence need {index}",
                "lexical_query": compact(facet.get("lexicalQuery"), 240),
                "field_queries": {
                    "key": compact(fields.get("key"), 240),
                    "summary": compact(fields.get("summary"), 240),
                    "text": compact(fields.get("text"), 240),
                    "heading": compact(fields.get("heading"), 240),
                },
                "corpora": facet.get("corpora") or route.get("corpora") or [],
            })
        return units
    fields = route.get("fieldQueries") or {}
    return [{
        "label": "Primary evidence",
        "lexical_query": (
            compact(route.get("lexicalQuery"), 240)
            or compact(route.get("naturalQuery"), 240)
        ),
        "field_queries": {
            "key": compact(fields.get("key"), 240),
            "summary": compact(fields.get("summary"), 240),
            "text": compact(fields.get("text"), 240),
            "heading": compact(fields.get("heading"), 240),
        },
        "corpora": route.get("corpora") or [],
    }]


def industry_tickers(
    route: dict[str, Any],
    entity_directory: list[dict[str, Any]],
) -> list[str]:
    industries = {
        compact(value, 120).casefold()
        for value in route.get("industries") or []
        if compact(value, 120)
    }
    if not industries:
        return []
    matches = []
    for entity in entity_directory:
        industry = compact(entity.get("industry"), 120).casefold()
        if industry in industries:
            matches.append(compact(entity.get("ticker"), 30).upper())
    return list(dict.fromkeys(filter(None, matches)))


def filters_for_route(
    route: dict[str, Any],
    entity_directory: list[dict[str, Any]],
    unit_corpora: list[str] | None = None,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    route_for_corpora = {**route, "corpora": unit_corpora or route.get("corpora") or []}
    corpora = normalized_corpora(route_for_corpora)
    if corpora:
        placeholders = ",".join("?" for _ in corpora)
        clauses.append(f"d.corpus IN ({placeholders})")
        values.extend(corpora)

    tickers = [
        compact(value, 30).upper()
        for value in route.get("tickers") or []
        if compact(value, 30)
    ]
    if not tickers:
        tickers = industry_tickers(route, entity_directory)
    if tickers:
        placeholders = ",".join("?" for _ in tickers)
        if any(corpus in {"macro", "authoritative"} for corpus in corpora):
            clauses.append(
                f"(d.corpus IN ('macro', 'authoritative') "
                f"OR d.entity IN ({placeholders}))"
            )
        else:
            clauses.append(f"d.entity IN ({placeholders})")
        values.extend(tickers)

    years = [
        str(value)
        for value in route.get("years") or []
        if isinstance(value, int) and 1900 <= value <= 2100
    ]
    if years:
        placeholders = ",".join("?" for _ in years)
        clauses.append(f"d.document_period IN ({placeholders})")
        values.extend(years)

    cadence = compact(route.get("filingCadence"), 30).casefold()
    if cadence in {"annual", "quarterly"}:
        clauses.append("(d.corpus <> 'filing' OR d.collection = ?)")
        values.append(cadence)
    return clauses, values


def search_lane(
    db: sqlite3.Connection,
    table: str,
    weights: str,
    column: str | None,
    query: str,
    route: dict[str, Any],
    unit: dict[str, Any],
    entity_directory: list[dict[str, Any]],
    lane: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    expression = fts_expression(query)
    if not expression:
        return []
    match_expression = f"{column} : ({expression})" if column else expression
    clauses, values = filters_for_route(
        route,
        entity_directory,
        unit.get("corpora"),
    )
    extra = f" AND {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute(
        f"""
        SELECT d.source_path, d.cohort, d.corpus, d.entity, d.collection,
               d.document_period, d.document_id, d.artifact_path,
               bm25({table}, {weights}) AS score
        FROM {table}
        JOIN docs d ON d.id = {table}.rowid
        WHERE {table} MATCH ? {extra}
        ORDER BY score
        LIMIT ?
        """,
        [match_expression, *values, limit],
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
            "facet": unit["label"],
            "lane": lane,
        }
        for row in rows
    ]


def rrf(facet_results: list[list[dict[str, Any]]], limit: int = 20) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for results in facet_results:
        for rank, result in enumerate(results, start=1):
            key = result["source_path"]
            item = fused.setdefault(
                key,
                {**result, "fusion_score": 0.0, "facet_ranks": {}, "facets": []},
            )
            item["fusion_score"] += 1 / (60 + rank)
            rank_key = f"{result['facet']}::{result.get('lane', 'unknown')}"
            item["facet_ranks"][rank_key] = rank
            if result["facet"] not in item["facets"]:
                item["facets"].append(result["facet"])
    return sorted(
        fused.values(),
        key=lambda item: (-item["fusion_score"], item["source_path"]),
    )[:limit]


def execute_plan(
    db: sqlite3.Connection,
    representation: str,
    route: dict[str, Any] | None,
    entity_directory: list[dict[str, Any]],
) -> dict[str, Any]:
    if not route:
        return {"units": [], "results": [], "error": "route_unavailable"}
    units = route_units(route)
    unit_logs = []
    all_lane_results = []
    for unit in units:
        fields = unit["field_queries"]
        lane_specs = [
            ("lexical", "baseline_fts", "3.0, 6.0, 0.5", None, unit["lexical_query"]),
            (
                "summary",
                "baseline_fts",
                "3.0, 6.0, 0.5",
                "current_key",
                fields["summary"] or unit["lexical_query"],
            ),
            (
                "text",
                "baseline_fts",
                "3.0, 6.0, 0.5",
                "content",
                fields["text"] or unit["lexical_query"],
            ),
            (
                "heading",
                "baseline_fts",
                "3.0, 6.0, 0.5",
                "content",
                fields["heading"] or unit["lexical_query"],
            ),
        ]
        if representation == "new":
            lane_specs.append((
                "generated_key",
                "key_fts",
                "3.0, 8.0, 5.0",
                None,
                fields["key"] or unit["lexical_query"],
            ))
        lane_logs = []
        for lane, table, weights, column, query in lane_specs:
            lane_results = search_lane(
                db,
                table,
                weights,
                column,
                query,
                route,
                unit,
                entity_directory,
                lane,
            )
            all_lane_results.append(lane_results)
            lane_logs.append({
                "lane": lane,
                "query": query,
                "result_count": len(lane_results),
                "results": lane_results,
            })
        unit_logs.append({**unit, "lanes": lane_logs})
    results = rrf(all_lane_results) if all_lane_results else []
    return {
        "units": unit_logs,
        "results": results,
        "error": None,
    }


def excerpt(markdown: str, query: str, limit: int = 3_200) -> str:
    text = markdown[:18_000]
    query_terms = tokens(query, limit=16)
    lowered = text.casefold()
    positions = [lowered.find(term) for term in query_terms if lowered.find(term) >= 0]
    if not positions:
        return text[:limit]
    windows = []
    for position in sorted(positions)[:4]:
        start = max(0, position - 500)
        windows.append(text[start:start + 1_200])
    return "\n\n[...]\n\n".join(windows)[:limit]


def candidate_payload(
    case: dict[str, Any],
    retrieval: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    unique: dict[str, dict[str, Any]] = {}
    for cell in CELL_NAMES:
        for result in retrieval[cell]["results"][:5]:
            unique[result["source_path"]] = result
    ordered = list(unique.values())
    random.Random(case["id"]).shuffle(ordered)
    visible = []
    mapping = {}
    for index, candidate in enumerate(ordered, start=1):
        candidate_id = f"C{index:02d}"
        markdown = (PROJECT_ROOT / candidate["source_path"]).read_text(
            encoding="utf-8",
            errors="replace",
        )
        visible.append({
            "candidate_id": candidate_id,
            "corpus": candidate["corpus"],
            "entity": candidate["entity"] or None,
            "document": candidate["document_id"],
            "period": candidate["document_period"] or None,
            "source_excerpt": excerpt(markdown, case["natural_query"]),
        })
        mapping[candidate_id] = candidate["source_path"]
    return visible, mapping


def judge_candidates(
    client: OpenAI,
    case: dict[str, Any],
    visible: list[dict[str, Any]],
    mapping: dict[str, str],
) -> dict[str, Any]:
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "page_relevance_judgment",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "judgments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "candidate_id": {"type": "string"},
                                "score": {"type": "integer", "minimum": 0, "maximum": 3},
                                "reason": {"type": "string"},
                            },
                            "required": ["candidate_id", "score", "reason"],
                        },
                    },
                },
                "required": ["judgments"],
            },
        },
    }
    prompt = "\n".join([
        "Judge source pages for an analyst's document-retrieval query.",
        "The ranking lane, generated page key, and prior score are hidden.",
        "Score each candidate independently:",
        "3 = directly answer-bearing evidence for the requested entity, period, and evidence need;",
        "2 = useful partial evidence;",
        "1 = related but generic, navigational, or insufficient;",
        "0 = irrelevant, wrong entity/corpus/period, or misleading.",
        "A cover, contents, generic policy, or broad chapter page is not answer-bearing unless the query asks for it.",
        f"QUERY: {case['natural_query']}",
        f"GOLD SCOPE: {json.dumps(case.get('params') or {}, ensure_ascii=False)}",
        f"CANDIDATES: {json.dumps(visible, ensure_ascii=False)}",
    ])
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        reasoning_effort="minimal",
        response_format=schema,
        messages=[{"role": "user", "content": prompt}],
        timeout=120,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    scores: dict[str, int] = {}
    reasons: dict[str, str] = {}
    for judgment in payload.get("judgments") or []:
        source_path = mapping.get(compact(judgment.get("candidate_id"), 20))
        if not source_path:
            continue
        scores[source_path] = int(judgment.get("score") or 0)
        reasons[source_path] = compact(judgment.get("reason"), 400)
    usage = response.usage
    return {
        "prompt_version": JUDGE_PROMPT_VERSION,
        "model": JUDGE_MODEL,
        "latency_ms": round((time.perf_counter() - started) * 1_000),
        "scores": scores,
        "reasons": reasons,
        "usage": {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        },
    }


def dcg(scores: list[int]) -> float:
    return sum((2**score - 1) / math.log2(index + 2) for index, score in enumerate(scores))


def score_cell(
    results: list[dict[str, Any]],
    judgment: dict[str, Any],
    shared_ideal_scores: list[int],
) -> dict[str, Any]:
    top = results[:5]
    scores = [int(judgment["scores"].get(item["source_path"], 0)) for item in top]
    padded = scores + [0] * (5 - len(scores))
    return {
        "scores": padded,
        "mean_relevance_at_5": sum(padded) / 5,
        "answer_bearing_precision_at_5": sum(score >= 2 for score in padded) / 5,
        "direct_page_at_1": bool(padded and padded[0] >= 2),
        "excellent_page_at_1": bool(padded and padded[0] == 3),
        "ndcg_at_5": (
            dcg(padded) / dcg(shared_ideal_scores)
            if dcg(shared_ideal_scores)
            else 0
        ),
    }


def route_scope_score(route: dict[str, Any] | None, case: dict[str, Any]) -> dict[str, Any]:
    if not route:
        return {"correct": False, "failures": ["route_unavailable"]}
    expected = case.get("params") or {}
    failures = []
    expected_entities = {
        value.strip().upper()
        for value in str(expected.get("tickers") or "").split(",")
        if value.strip()
    }
    routed_entities = {compact(value, 30).upper() for value in route.get("tickers") or []}
    if expected_entities and routed_entities != expected_entities:
        failures.append(
            f"entities expected={sorted(expected_entities)} actual={sorted(routed_entities)}"
        )
    expected_corpora = {
        value.strip().casefold()
        for value in str(expected.get("corpora") or "").split(",")
        if value.strip()
    }
    routed_corpora = {compact(value, 30).casefold() for value in route.get("corpora") or []}
    if expected_corpora and not expected_corpora.issubset(routed_corpora):
        failures.append(
            f"corpora expected={sorted(expected_corpora)} actual={sorted(routed_corpora)}"
        )
    expected_cadence = compact(expected.get("filing_cadence"), 30).casefold()
    routed_cadence = compact(route.get("filingCadence"), 30).casefold()
    if expected_cadence in {"annual", "quarterly"} and routed_cadence != expected_cadence:
        failures.append(f"cadence expected={expected_cadence} actual={routed_cadence or 'missing'}")
    return {"correct": not failures, "failures": failures}


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for cell in CELL_NAMES:
        scored = [case["scores"][cell] for case in cases]
        output[cell] = {
            "cases": len(scored),
            "mean_relevance_at_5": round(
                sum(value["mean_relevance_at_5"] for value in scored) / len(scored), 4
            ),
            "answer_bearing_precision_at_5": round(
                sum(value["answer_bearing_precision_at_5"] for value in scored) / len(scored), 4
            ),
            "direct_page_at_1": round(
                sum(value["direct_page_at_1"] for value in scored) / len(scored), 4
            ),
            "excellent_page_at_1": round(
                sum(value["excellent_page_at_1"] for value in scored) / len(scored), 4
            ),
            "mean_ndcg_at_5": round(
                sum(value["ndcg_at_5"] for value in scored) / len(scored), 4
            ),
        }
    return output


def mean_delta(
    cases: list[dict[str, Any]],
    right: str,
    left: str,
    metric: str,
) -> float:
    return round(
        sum(
            float(case["scores"][right][metric]) - float(case["scores"][left][metric])
            for case in cases
        ) / len(cases),
        4,
    )


def movement(cases: list[dict[str, Any]], right: str, left: str) -> dict[str, Any]:
    wins = ties = losses = 0
    per_case = []
    for case in cases:
        delta = (
            case["scores"][right]["ndcg_at_5"]
            - case["scores"][left]["ndcg_at_5"]
        )
        if delta > 1e-9:
            wins += 1
        elif delta < -1e-9:
            losses += 1
        else:
            ties += 1
        per_case.append({
            "id": case["id"],
            "query": case["query"],
            "delta_ndcg_at_5": round(delta, 4),
            "delta_direct_page_at_1": (
                int(case["scores"][right]["direct_page_at_1"])
                - int(case["scores"][left]["direct_page_at_1"])
            ),
        })
    return {
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "mean_delta_ndcg_at_5": mean_delta(cases, right, left, "ndcg_at_5"),
        "mean_delta_answer_bearing_precision_at_5": mean_delta(
            cases, right, left, "answer_bearing_precision_at_5"
        ),
        "mean_delta_direct_page_at_1": mean_delta(
            cases, right, left, "direct_page_at_1"
        ),
        "largest_gains": sorted(
            per_case,
            key=lambda item: (-item["delta_ndcg_at_5"], item["id"]),
        )[:8],
        "largest_losses": sorted(
            per_case,
            key=lambda item: (item["delta_ndcg_at_5"], item["id"]),
        )[:8],
    }


def markdown_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Router × Page Representation Factorial Evaluation",
        "",
        f"Run: `{report['run_id']}`",
        "",
        "## Boundary",
        "",
        "- Candidate universe: the exact same 20,000 local page records in every cell.",
        f"- Old router: `{report['boundary']['old_router_commit']}`.",
        f"- New router: `{report['boundary']['new_router_commit']}`.",
        "- Old representation: metadata + existing `search_text` + original Markdown.",
        "- New representation: the old fields plus generated `retrieval_key` and `retrieval_phrases`.",
        "- `display_summary`, parse quality, metadata alignment, and grounding anchors are not indexed.",
        "- Relevance is judged from source Markdown with router/index identity hidden.",
        "",
        "## Results",
        "",
        "| Cell | nDCG@5 | Answer-bearing P@5 | Direct page @1 | Excellent page @1 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    labels = {
        "old_router_old_representation": "Old router + old representation",
        "old_router_new_representation": "Old router + new representation",
        "new_router_old_representation": "New router + old representation",
        "new_router_new_representation": "New router + new representation",
    }
    for cell in CELL_NAMES:
        value = metrics[cell]
        lines.append(
            f"| {labels[cell]} | {value['mean_ndcg_at_5']:.3f} | "
            f"{value['answer_bearing_precision_at_5']:.3f} | "
            f"{value['direct_page_at_1']:.1%} | {value['excellent_page_at_1']:.1%} |"
        )
    lines.extend([
        "",
        "## Attribution",
        "",
    ])
    for name, value in report["effects"].items():
        lines.extend([
            f"### {name.replace('_', ' ').title()}",
            "",
            f"- nDCG@5: {value['mean_delta_ndcg_at_5']:+.4f}",
            f"- Answer-bearing P@5: {value['mean_delta_answer_bearing_precision_at_5']:+.4f}",
            f"- Direct page @1: {value['mean_delta_direct_page_at_1']:+.4f}",
            f"- Cases: {value['wins']} wins / {value['ties']} ties / {value['losses']} losses",
            "",
        ])
    lines.extend([
        "## Router Scope",
        "",
        f"- Old router correct: {report['router_scope']['old_correct']}/{report['router_scope']['cases']}.",
        f"- New router correct: {report['router_scope']['new_correct']}/{report['router_scope']['cases']}.",
        "",
        "## Intermediate Artifacts",
        "",
        "- `routes/`: raw persisted old and new route plans.",
        "- `retrieval/`: per-facet result lists and fused rankings for all four cells.",
        "- `judgments/`: blind source-page judgments.",
        "- `cases/`: scored cells and attribution per query.",
        "",
        "## Limitations",
        "",
    ])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-router-base", default="http://localhost:4311")
    parser.add_argument("--new-router-base", default="http://localhost:4312")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--routes-from",
        help="Reuse persisted route artifacts from an earlier factorial run.",
    )
    parser.add_argument(
        "--judgments-from",
        help="Reuse prior blind judgments when they cover every current candidate.",
    )
    parser.add_argument("--skip-judge", action="store_true")
    args = parser.parse_args()

    if not INDEX_PATH.is_file():
        raise SystemExit(f"Missing 20k index: {INDEX_PATH}")
    load_dotenv(PROJECT_ROOT / ".env")
    cases = load_cases()
    requested = set(args.case)
    if requested:
        cases = [case for case in cases if case["id"] in requested]
    if not cases:
        raise SystemExit("No evaluation cases selected")

    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    run_root = RUNS_ROOT / run_id
    for folder in ("routes", "retrieval", "judgments", "cases"):
        (run_root / folder).mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "started_at": now_iso(),
        "status": "running",
        "cases": len(cases),
        "boundary": {
            "old_router_commit": "e094ca83b26c185448590a733eb15e7c0fe7f4e3",
            "new_router_commit": "944aff9ac3394e8f565f5d2f5527a0a8c0ea1dc8",
            "candidate_pages": 20_000,
            "index": str(INDEX_PATH.relative_to(PROJECT_ROOT)),
        },
        "stages": {},
    }
    write_json(run_root / "run_manifest.json", manifest)

    # Entity directory is only needed for industry expansion. Fetch it directly
    # from the stable corpus API rather than deriving entities from route output.
    entity_request = Request(
        "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev/api/entities",
        headers={"User-Agent": "psxgpt-corpus-factorial-eval/1.0"},
    )
    with urlopen(entity_request, timeout=30) as response:
        entity_directory = (
            json.loads(response.read().decode("utf-8")).get("entities") or []
        )
    if not entity_directory:
        raise RuntimeError("Entity directory is empty; industry routing cannot be evaluated.")
    manifest["entity_directory"] = {
        "records": len(entity_directory),
        "source": "production corpus API /api/entities",
    }
    write_json(run_root / "run_manifest.json", manifest)

    routes: dict[str, Any] = {}
    route_counts = Counter()
    route_started = time.perf_counter()
    source_route_root = None
    if args.routes_from:
        source_route_root = Path(args.routes_from)
        if not source_route_root.is_absolute():
            source_route_root = PROJECT_ROOT / source_route_root
    for index, case in enumerate(cases, start=1):
        if source_route_root:
            payload = json.loads(
                (source_route_root / "routes" / f"{case['id']}.json").read_text(
                    encoding="utf-8"
                )
            )
            old = payload["old_router"]
            new = payload["new_router"]
        else:
            old = route_case(args.old_router_base, case)
            new = route_case(args.new_router_base, case)
            payload = {
                "case": {
                    "id": case["id"],
                    "family": case["family"],
                    "query": case["natural_query"],
                    "gold_scope": case.get("params") or {},
                },
                "old_router": old,
                "new_router": new,
            }
        routes[case["id"]] = payload
        write_json(run_root / "routes" / f"{case['id']}.json", payload)
        route_counts["old_ok"] += old["http_status"] == 200
        route_counts["new_ok"] += new["http_status"] == 200
        print(
            f"[route {index}/{len(cases)}] {case['id']} "
            f"old={old['http_status']} new={new['http_status']} "
            f"source={'persisted' if source_route_root else 'live'}",
            flush=True,
        )
    manifest["stages"]["routing"] = {
        "completed_at": now_iso(),
        "elapsed_ms": round((time.perf_counter() - route_started) * 1_000),
        "source_run": (
            str(source_route_root.relative_to(PROJECT_ROOT))
            if source_route_root
            else None
        ),
        **route_counts,
    }
    write_json(run_root / "run_manifest.json", manifest)

    db = sqlite3.connect(INDEX_PATH)
    retrieval_by_case: dict[str, Any] = {}
    retrieval_started = time.perf_counter()
    for index, case in enumerate(cases, start=1):
        route_log = routes[case["id"]]
        old_route = route_log["old_router"]["plan"]
        new_route = route_log["new_router"]["plan"]
        retrieval = {
            "old_router_old_representation": execute_plan(
                db, "old", old_route, entity_directory
            ),
            "old_router_new_representation": execute_plan(
                db, "new", old_route, entity_directory
            ),
            "new_router_old_representation": execute_plan(
                db, "old", new_route, entity_directory
            ),
            "new_router_new_representation": execute_plan(
                db, "new", new_route, entity_directory
            ),
        }
        payload = {
            "case": {
                "id": case["id"],
                "family": case["family"],
                "query": case["natural_query"],
            },
            "retrieval": retrieval,
        }
        retrieval_by_case[case["id"]] = retrieval
        write_json(run_root / "retrieval" / f"{case['id']}.json", payload)
        print(
            f"[retrieve {index}/{len(cases)}] {case['id']} "
            + " ".join(
                f"{cell}={len(retrieval[cell]['results'])}"
                for cell in CELL_NAMES
            ),
            flush=True,
        )
    manifest["stages"]["retrieval"] = {
        "completed_at": now_iso(),
        "elapsed_ms": round((time.perf_counter() - retrieval_started) * 1_000),
    }
    write_json(run_root / "run_manifest.json", manifest)

    if args.skip_judge:
        print(run_root)
        return 0
    if not os.getenv("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY is required for blind page judging")
    client = OpenAI(
        api_key=os.environ["GEMINI_API_KEY"],
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        timeout=120,
        max_retries=1,
    )
    judgments: dict[str, Any] = {}
    judge_started = time.perf_counter()
    source_judgment_root = None
    if args.judgments_from:
        source_judgment_root = Path(args.judgments_from)
        if not source_judgment_root.is_absolute():
            source_judgment_root = PROJECT_ROOT / source_judgment_root
    reused_judgments = 0
    for index, case in enumerate(cases, start=1):
        visible, mapping = candidate_payload(case, retrieval_by_case[case["id"]])
        judgment = None
        if source_judgment_root:
            prior_path = source_judgment_root / "judgments" / f"{case['id']}.json"
            if prior_path.is_file():
                prior = json.loads(prior_path.read_text(encoding="utf-8"))
                if all(source_path in (prior.get("scores") or {}) for source_path in mapping.values()):
                    judgment = {
                        **prior,
                        "reused_from": str(prior_path.relative_to(PROJECT_ROOT)),
                    }
                    reused_judgments += 1
        if judgment is None:
            judgment = judge_candidates(client, case, visible, mapping)
        judgments[case["id"]] = judgment
        write_json(run_root / "judgments" / f"{case['id']}.json", judgment)
        print(
            f"[judge {index}/{len(cases)}] {case['id']} "
            f"candidates={len(visible)} tokens={judgment['usage']['total_tokens']} "
            f"source={'reused' if judgment.get('reused_from') else 'live'}",
            flush=True,
        )
    manifest["stages"]["judging"] = {
        "completed_at": now_iso(),
        "elapsed_ms": round((time.perf_counter() - judge_started) * 1_000),
        "evidence_prompt_tokens": sum(
            item["usage"]["prompt_tokens"] for item in judgments.values()
        ),
        "evidence_completion_tokens": sum(
            item["usage"]["completion_tokens"] for item in judgments.values()
        ),
        "live_prompt_tokens": sum(
            item["usage"]["prompt_tokens"]
            for item in judgments.values()
            if not item.get("reused_from")
        ),
        "live_completion_tokens": sum(
            item["usage"]["completion_tokens"]
            for item in judgments.values()
            if not item.get("reused_from")
        ),
        "reused_cases": reused_judgments,
        "source_run": (
            str(source_judgment_root.relative_to(PROJECT_ROOT))
            if source_judgment_root
            else None
        ),
    }
    write_json(run_root / "run_manifest.json", manifest)

    case_outputs = []
    for case in cases:
        retrieval = retrieval_by_case[case["id"]]
        judgment = judgments[case["id"]]
        shared_ideal_scores = sorted(
            [
                int(judgment["scores"].get(result["source_path"], 0))
                for cell in CELL_NAMES
                for result in retrieval[cell]["results"][:5]
            ],
            reverse=True,
        )[:5]
        shared_ideal_scores += [0] * (5 - len(shared_ideal_scores))
        scores = {
            cell: score_cell(
                retrieval[cell]["results"],
                judgment,
                shared_ideal_scores,
            )
            for cell in CELL_NAMES
        }
        route_scope = {
            "old": route_scope_score(
                routes[case["id"]]["old_router"]["plan"], case
            ),
            "new": route_scope_score(
                routes[case["id"]]["new_router"]["plan"], case
            ),
        }
        output = {
            "id": case["id"],
            "family": case["family"],
            "query": case["natural_query"],
            "route_scope": route_scope,
            "scores": scores,
            "effects": {
                "representation_under_old_router_ndcg": round(
                    scores["old_router_new_representation"]["ndcg_at_5"]
                    - scores["old_router_old_representation"]["ndcg_at_5"],
                    4,
                ),
                "representation_under_new_router_ndcg": round(
                    scores["new_router_new_representation"]["ndcg_at_5"]
                    - scores["new_router_old_representation"]["ndcg_at_5"],
                    4,
                ),
                "router_on_old_representation_ndcg": round(
                    scores["new_router_old_representation"]["ndcg_at_5"]
                    - scores["old_router_old_representation"]["ndcg_at_5"],
                    4,
                ),
                "router_on_new_representation_ndcg": round(
                    scores["new_router_new_representation"]["ndcg_at_5"]
                    - scores["old_router_new_representation"]["ndcg_at_5"],
                    4,
                ),
                "end_to_end_ndcg": round(
                    scores["new_router_new_representation"]["ndcg_at_5"]
                    - scores["old_router_old_representation"]["ndcg_at_5"],
                    4,
                ),
            },
        }
        case_outputs.append(output)
        write_json(run_root / "cases" / f"{case['id']}.json", output)

    effects = {
        "representation_effect_under_old_router": movement(
            case_outputs,
            "old_router_new_representation",
            "old_router_old_representation",
        ),
        "representation_effect_under_new_router": movement(
            case_outputs,
            "new_router_new_representation",
            "new_router_old_representation",
        ),
        "router_effect_on_old_representation": movement(
            case_outputs,
            "new_router_old_representation",
            "old_router_old_representation",
        ),
        "router_effect_on_new_representation": movement(
            case_outputs,
            "new_router_new_representation",
            "old_router_new_representation",
        ),
        "end_to_end_effect": movement(
            case_outputs,
            "new_router_new_representation",
            "old_router_old_representation",
        ),
    }
    report = {
        "run_id": run_id,
        "generated_at": now_iso(),
        "boundary": manifest["boundary"],
        "cases": len(case_outputs),
        "metrics": aggregate(case_outputs),
        "effects": effects,
        "router_scope": {
            "cases": len(case_outputs),
            "old_correct": sum(case["route_scope"]["old"]["correct"] for case in case_outputs),
            "new_correct": sum(case["route_scope"]["new"]["correct"] for case in case_outputs),
            "old_failures": [
                {
                    "id": case["id"],
                    "query": case["query"],
                    "failures": case["route_scope"]["old"]["failures"],
                }
                for case in case_outputs
                if not case["route_scope"]["old"]["correct"]
            ],
            "new_failures": [
                {
                    "id": case["id"],
                    "query": case["query"],
                    "failures": case["route_scope"]["new"]["failures"],
                }
                for case in case_outputs
                if not case["route_scope"]["new"]["correct"]
            ],
        },
        "judge": {
            "model": JUDGE_MODEL,
            "prompt_version": JUDGE_PROMPT_VERSION,
            "usage": manifest["stages"]["judging"],
        },
        "limitations": [
            "The relevance labels are blind model judgments, not human qrels.",
            "The 20,000-page universe contains all sampled non-filing records but only a query-centred subset of recent filing pages.",
            "Old representation has existing search_text on 8,971 pages; the remaining 11,029 pages rely on metadata and raw Markdown.",
            "The experiment isolates retrieval within the fixed 20,000 pages and does not estimate full-corpus coverage.",
            "Display summaries and quality-control fields are intentionally excluded because the proposed primary Key index searches retrieval_key and retrieval_phrases only.",
        ],
    }
    write_json(run_root / "eval_report.json", report)
    (run_root / "eval_report.md").write_text(markdown_report(report), encoding="utf-8")
    manifest["status"] = "complete"
    manifest["completed_at"] = now_iso()
    manifest["stages"]["scoring"] = {"completed_at": manifest["completed_at"]}
    write_json(run_root / "run_manifest.json", manifest)
    print(json.dumps({
        "run": str(run_root.relative_to(PROJECT_ROOT)),
        "metrics": report["metrics"],
        "effects": report["effects"],
        "router_scope": report["router_scope"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
