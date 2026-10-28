#!/usr/bin/env python3
"""Generate compact Page Index V3 keys with an explicit spend ceiling."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "page_index_v3_20k"
PAGE_ROOT = OUTPUT_ROOT / "pages"
SAMPLE_PATH = OUTPUT_ROOT / "sample.jsonl"
REPORT_PATH = PROJECT_ROOT / "artifacts" / "reports" / "page_index_v3_enrichment.json"
PROMPT_PATH = EXPERIMENT_ROOT / "prompt.md"

MODEL = "deepseek-v4-flash"
SCHEMA_VERSION = "page-index.v3"
PROMPT_VERSION = "2026-07-29.2"
PRICE_PER_MILLION = {"input": 0.14, "output": 0.28}
MAX_INPUT_CHARS = 18_000
MAX_OUTPUT_TOKENS = 1_400
MAX_KEY_WORDS = 110
MAX_SUMMARY_WORDS = 55
MAX_PHRASES = 5
MAX_ANCHORS = 8
ALLOWED_PARSE_STATUS = {"ok", "incomplete", "reextract"}
ALLOWED_ALIGNMENT = {"aligned", "unclear", "conflict"}


def compact(value: Any, limit: int = 2_000) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def bounded_words(value: Any, limit: int) -> str:
    return " ".join(compact(value, 4_000).split()[:limit])


def normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bounded_list(value: Any, count: int, word_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:count]:
        text = bounded_words(item, word_limit)
        if text and text not in result:
            result.append(text)
    return result


def validate_output(
    payload: Any,
    supplied_markdown: str,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")
    errors = []
    quality = payload.get("parse_quality")
    if not isinstance(quality, dict):
        quality = {}
        errors.append("parse_quality_missing")
    parse_status = compact(quality.get("status"), 30).casefold()
    if parse_status not in ALLOWED_PARSE_STATUS:
        parse_status = "incomplete"
        errors.append("parse_quality_status_invalid")

    alignment = payload.get("metadata_alignment")
    if not isinstance(alignment, dict):
        alignment = {}
        errors.append("metadata_alignment_missing")
    alignment_status = compact(alignment.get("status"), 30).casefold()
    if alignment_status not in ALLOWED_ALIGNMENT:
        alignment_status = "unclear"
        errors.append("metadata_alignment_status_invalid")

    retrieval_key = bounded_words(payload.get("retrieval_key"), MAX_KEY_WORDS)
    display_summary = bounded_words(payload.get("display_summary"), MAX_SUMMARY_WORDS)
    phrases = bounded_list(payload.get("retrieval_phrases"), MAX_PHRASES, 14)
    raw_anchors = bounded_list(payload.get("grounding_anchors"), MAX_ANCHORS, 45)
    normalized_markdown = normalized_text(supplied_markdown)
    grounded_anchors = [
        anchor
        for anchor in raw_anchors
        if normalized_text(anchor) in normalized_markdown
    ]
    if len(grounded_anchors) != len(raw_anchors):
        errors.append("ungrounded_anchor_excluded")
    if parse_status == "ok" and not retrieval_key:
        errors.append("ok_without_retrieval_key")

    eligible = (
        parse_status != "reextract"
        and alignment_status != "conflict"
        and bool(retrieval_key)
        and bool(grounded_anchors)
    )
    normalized = {
        "parse_quality": {
            "status": parse_status,
            "issues": bounded_list(quality.get("issues"), 8, 24),
        },
        "metadata_alignment": {
            "status": alignment_status,
            "reason": bounded_words(alignment.get("reason"), 45),
        },
        "retrieval_key": retrieval_key,
        "retrieval_phrases": phrases,
        "display_summary": display_summary,
        "grounding_anchors": raw_anchors,
        "grounded_anchors": grounded_anchors,
        "eligible_for_key_index": eligible,
    }
    metrics = {
        "anchors": len(raw_anchors),
        "grounded_anchors": len(grounded_anchors),
        "retrieval_key_words": len(retrieval_key.split()),
        "display_summary_words": len(display_summary.split()),
        "retrieval_phrases": len(phrases),
    }
    return normalized, errors, metrics


def input_slice(markdown: str, cohort: str) -> tuple[str, str]:
    if len(markdown) <= MAX_INPUT_CHARS:
        return markdown, "complete"
    if cohort == "announcement":
        return (
            markdown[:14_000]
            + "\n\n[...middle of source omitted for bounded indexing pass...]\n\n"
            + markdown[-4_000:],
            "announcement_head_and_tail",
        )
    return markdown[:MAX_INPUT_CHARS], "page_prefix"


def artifact_path(item: dict[str, Any]) -> Path:
    return PAGE_ROOT / f"{item['artifact_id']}.json"


def existing_artifacts() -> list[dict[str, Any]]:
    rows = []
    for path in PAGE_ROOT.glob("*.json"):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def run_one(
    client: OpenAI,
    prompt: str,
    item: dict[str, Any],
    reset: bool,
) -> dict[str, Any]:
    target = artifact_path(item)
    prior_usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    prior_attempts = 0
    if target.exists() and not reset:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if (
                existing.get("status") == "ok"
                and existing.get("input_sha256") == item["input_sha256"]
                and existing.get("prompt_version") == PROMPT_VERSION
                and existing.get("model") == MODEL
            ):
                return {**existing, "_cached": True}
            if (
                existing.get("input_sha256") == item["input_sha256"]
                and existing.get("prompt_version") == PROMPT_VERSION
                and existing.get("model") == MODEL
            ):
                prior_usage = existing.get("usage") or prior_usage
                prior_attempts = int(existing.get("attempts", 0) or 0)
        except (OSError, json.JSONDecodeError):
            pass

    markdown = (PROJECT_ROOT / item["source_path"]).read_text(
        encoding="utf-8", errors="replace"
    )
    body, input_strategy = input_slice(markdown, item["cohort"])
    user_content = (
        f"{prompt}\n\n"
        "TRUSTED DOCUMENT METADATA\n"
        f"{json.dumps(item['trusted_metadata'], ensure_ascii=False, sort_keys=True)}\n\n"
        "SOURCE MARKDOWN\n"
        f"{body}"
    )
    started = time.perf_counter()
    last_error = ""
    cumulative_input_tokens = int(prior_usage.get("input_tokens", 0) or 0)
    cumulative_output_tokens = int(prior_usage.get("output_tokens", 0) or 0)
    cumulative_cost = float(prior_usage.get("cost_usd", 0) or 0)
    prior_cost = cumulative_cost
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=0,
                max_tokens=MAX_OUTPUT_TOKENS,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "Create one compact, grounded page retrieval key as strict JSON.",
                    },
                    {"role": "user", "content": user_content},
                ],
            )
            raw_content = response.choices[0].message.content or ""
            usage = response.usage
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            cost = (
                input_tokens * PRICE_PER_MILLION["input"]
                + output_tokens * PRICE_PER_MILLION["output"]
            ) / 1_000_000
            cumulative_input_tokens += input_tokens
            cumulative_output_tokens += output_tokens
            cumulative_cost += cost
            raw_payload = json.loads(raw_content)
            output, validation_errors, metrics = validate_output(raw_payload, body)
            artifact = {
                "schema_version": SCHEMA_VERSION,
                "prompt_version": PROMPT_VERSION,
                "prompt_sha256": sha256_text(prompt),
                "model": MODEL,
                "provider": "deepseek",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "page_key": item["page_key"],
                "source_path": item["source_path"],
                "cohort": item["cohort"],
                "trusted_metadata": item["trusted_metadata"],
                "current_search_text": item.get("current_search_text") or "",
                "selection_reason": item["selection_reason"],
                "selection_queries": item.get("selection_queries") or [],
                "input_sha256": item["input_sha256"],
                "input_chars": item["input_chars"],
                "input_strategy": input_strategy,
                "input_truncated": input_strategy != "complete",
                "attempts": prior_attempts + attempt,
                "latency_ms": round((time.perf_counter() - started) * 1_000),
                "usage": {
                    "input_tokens": cumulative_input_tokens,
                    "output_tokens": cumulative_output_tokens,
                    "cost_usd": round(cumulative_cost, 8),
                },
                "validation_errors": validation_errors,
                "validation_metrics": metrics,
                "output": output,
                "raw_response": raw_payload,
                "status": "ok",
            }
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return {
                **artifact,
                "_new_cost_usd": round(cumulative_cost - prior_cost, 8),
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            if attempt < 3:
                time.sleep(min(8, attempt * 2))
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "page_key": item["page_key"],
        "source_path": item["source_path"],
        "cohort": item["cohort"],
        "trusted_metadata": item["trusted_metadata"],
        "current_search_text": item.get("current_search_text") or "",
        "selection_reason": item["selection_reason"],
        "selection_queries": item.get("selection_queries") or [],
        "input_sha256": item["input_sha256"],
        "input_chars": item["input_chars"],
        "input_strategy": input_strategy,
        "input_truncated": input_strategy != "complete",
        "attempts": prior_attempts + 3,
        "latency_ms": round((time.perf_counter() - started) * 1_000),
        "usage": {
            "input_tokens": cumulative_input_tokens,
            "output_tokens": cumulative_output_tokens,
            "cost_usd": round(cumulative_cost, 8),
        },
        "validation_errors": [],
        "validation_metrics": {},
        "output": None,
        "raw_response": None,
        "status": "error",
        "error": last_error,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **artifact,
        "_new_cost_usd": round(cumulative_cost - prior_cost, 8),
    }


def build_report(sample: list[dict[str, Any]], budget_usd: float) -> dict[str, Any]:
    artifacts = existing_artifacts()
    successful = [row for row in artifacts if row.get("status") == "ok" and row.get("output")]
    costs = sum(float(row.get("usage", {}).get("cost_usd", 0)) for row in artifacts)
    total_anchors = sum(
        int(row.get("validation_metrics", {}).get("anchors", 0))
        for row in successful
    )
    grounded_anchors = sum(
        int(row.get("validation_metrics", {}).get("grounded_anchors", 0))
        for row in successful
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "sample_size": len(sample),
        "artifacts": len(artifacts),
        "statuses": dict(sorted(Counter(row.get("status", "missing") for row in artifacts).items())),
        "parse_statuses": dict(sorted(Counter(
            row.get("output", {}).get("parse_quality", {}).get("status", "missing")
            for row in successful
        ).items())),
        "metadata_alignment": dict(sorted(Counter(
            row.get("output", {}).get("metadata_alignment", {}).get("status", "missing")
            for row in successful
        ).items())),
        "eligible_for_key_index": sum(
            bool(row.get("output", {}).get("eligible_for_key_index"))
            for row in successful
        ),
        "anchors": {
            "total": total_anchors,
            "grounded": grounded_anchors,
            "grounding_rate": round(grounded_anchors / total_anchors, 4)
            if total_anchors else None,
        },
        "validation_errors": dict(sorted(Counter(
            error
            for row in successful
            for error in row.get("validation_errors", [])
        ).items())),
        "input_truncated": sum(bool(row.get("input_truncated")) for row in successful),
        "usage": {
            "input_tokens": sum(int(row.get("usage", {}).get("input_tokens", 0)) for row in artifacts),
            "output_tokens": sum(int(row.get("usage", {}).get("output_tokens", 0)) for row in artifacts),
            "cost_usd": round(costs, 6),
            "budget_usd": budget_usd,
            "remaining_usd": round(max(0, budget_usd - costs), 6),
        },
        "by_cohort": {
            cohort: {
                "artifacts": len(rows),
                "eligible": sum(
                    bool(row.get("output", {}).get("eligible_for_key_index"))
                    for row in rows
                ),
            }
            for cohort, rows in sorted({
                cohort: [row for row in successful if row["cohort"] == cohort]
                for cohort in {row["cohort"] for row in successful}
            }.items())
        },
        "sample_path": str(SAMPLE_PATH.relative_to(PROJECT_ROOT)),
        "page_root": str(PAGE_ROOT.relative_to(PROJECT_ROOT)),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=128)
    parser.add_argument("--budget-usd", type=float, default=17.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--smoke-per-cohort", type=int)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    if not SAMPLE_PATH.is_file():
        raise SystemExit("Run prepare_sample.py first")
    sample = [
        json.loads(line)
        for line in SAMPLE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.smoke_per_cohort:
        active = []
        cohort_counts = Counter()
        for item in sample:
            if cohort_counts[item["cohort"]] >= args.smoke_per_cohort:
                continue
            active.append(item)
            cohort_counts[item["cohort"]] += 1
    else:
        active = sample[:args.limit] if args.limit else sample
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required")
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
        timeout=90,
        max_retries=0,
    )
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    already_spent = sum(
        float(row.get("usage", {}).get("cost_usd", 0))
        for row in existing_artifacts()
        if row.get("prompt_version") == PROMPT_VERSION
    )
    reserve = 0.25
    print(
        f"Compact-key enrichment: {len(active):,} records; "
        f"${already_spent:.4f} already spent; ${args.budget_usd:.2f} cap"
    )
    lock = threading.Lock()
    processed = 0
    stopped_for_budget = False
    current_spend = already_spent
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        with tqdm(total=len(active), desc="page-index-v3") as progress:
            iterator = iter(active)
            futures = {}

            def submit_next() -> bool:
                try:
                    item = next(iterator)
                except StopIteration:
                    return False
                future = pool.submit(run_one, client, prompt, item, args.reset)
                futures[future] = item
                return True

            for _ in range(min(max(1, args.workers), len(active))):
                submit_next()

            while futures:
                completed, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in completed:
                    futures.pop(future)
                    result = future.result()
                    if not result.get("_cached"):
                        current_spend += float(result.get("_new_cost_usd", 0))
                    with lock:
                        processed += 1
                        progress.update(1)
                    if processed % 1_000 == 0:
                        build_report(sample, args.budget_usd)

                    if current_spend >= args.budget_usd - reserve:
                        stopped_for_budget = True
                    elif not stopped_for_budget:
                        submit_next()

    report = build_report(sample, args.budget_usd)
    report["stopped_for_budget"] = stopped_for_budget
    report["processed_this_invocation"] = processed
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if stopped_for_budget and report["artifacts"] < len(active) else 0


if __name__ == "__main__":
    raise SystemExit(main())
