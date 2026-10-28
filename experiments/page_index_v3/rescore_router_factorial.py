#!/usr/bin/env python3
"""Rebuild factorial scores from persisted routes, retrieval, and judgments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluate_router_factorial import (
    CELL_NAMES,
    PROJECT_ROOT,
    aggregate,
    load_cases,
    markdown_report,
    movement,
    now_iso,
    route_scope_score,
    score_cell,
    write_json,
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    args = parser.parse_args()
    run_root = Path(args.run)
    if not run_root.is_absolute():
        run_root = PROJECT_ROOT / run_root
    manifest = read_json(run_root / "run_manifest.json")
    case_map = {case["id"]: case for case in load_cases()}
    case_outputs = []
    for route_path in sorted((run_root / "routes").glob("*.json")):
        case_id = route_path.stem
        case = case_map[case_id]
        routes = read_json(route_path)
        retrieval = read_json(run_root / "retrieval" / f"{case_id}.json")["retrieval"]
        judgment = read_json(run_root / "judgments" / f"{case_id}.json")
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
            "old": route_scope_score(routes["old_router"]["plan"], case),
            "new": route_scope_score(routes["new_router"]["plan"], case),
        }
        output = {
            "id": case_id,
            "family": case["family"],
            "query": case["natural_query"],
            "shared_ideal_scores": shared_ideal_scores,
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
        write_json(run_root / "cases" / f"{case_id}.json", output)

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
    prior = read_json(run_root / "eval_report.json")
    report = {
        **prior,
        "rescored_at": now_iso(),
        "scoring_version": "shared-observed-pool-ideal.v2",
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
    }
    write_json(run_root / "eval_report.json", report)
    (run_root / "eval_report.md").write_text(markdown_report(report), encoding="utf-8")
    manifest["stages"]["rescoring"] = {
        "completed_at": report["rescored_at"],
        "scoring_version": report["scoring_version"],
    }
    write_json(run_root / "run_manifest.json", manifest)
    print(json.dumps({
        "run": str(run_root.relative_to(PROJECT_ROOT)),
        "metrics": report["metrics"],
        "effects": report["effects"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
