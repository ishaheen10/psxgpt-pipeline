#!/usr/bin/env python3
"""Build and run the Page Index V2 1,000-page enrichment pilot.

The experiment is additive and resumable. It reads existing Markdown, writes
versioned sidecars, and never mutates D1 or the current review/search artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import OpenAI
from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "page_index_v2_pilot"
PAGE_OUTPUT_ROOT = OUTPUT_ROOT / "pages"
SAMPLE_PATH = OUTPUT_ROOT / "sample.jsonl"
REPORT_PATH = PROJECT_ROOT / "artifacts" / "reports" / "page_index_v2_pilot.json"
PROMPT_PATH = EXPERIMENT_ROOT / "prompt.md"

MODEL = "deepseek-v4-flash"
SCHEMA_VERSION = "page-index.v2"
PROMPT_VERSION = "2026-07-29.1"
SEED = 20260729
MAX_INPUT_CHARS = 16_000
MAX_BLOCKS = 8
MAX_ANCHORS_PER_BLOCK = 12
MAX_LIST_VALUES = 16

QUOTAS = {
    "filing_statement": 160,
    "filing_other": 340,
    "economic_survey": 150,
    "macro_other": 100,
    "announcement": 150,
    "authoritative": 100,
}

STATEMENT_TAGS = {
    "profit_loss",
    "balance_sheet",
    "cash_flow",
    "changes_in_equity",
    "comprehensive_income",
}
ALLOWED_PARSE_STATUS = {"ok", "incomplete", "reextract"}
ALLOWED_DATA_SHAPES = {"table", "chart", "narrative", "list", "mixed"}
PRICE_PER_MILLION = {"input": 0.14, "output": 0.28}


def compact(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def artifact_id(source_path: str) -> str:
    return sha256_text(source_path)[:20]


def page_number(path: Path) -> int | None:
    digits = "".join(character for character in path.stem if character.isdigit())
    return int(digits) if digits else None


def filing_cadence(document: str) -> str:
    return "quarterly" if "quarterly" in document.casefold() else "annual"


def statement_names(record: dict[str, Any]) -> list[str]:
    names = []
    for item in record.get("section_tags") or []:
        if not isinstance(item, dict):
            continue
        name = compact(item.get("tag"), 80).casefold()
        if name in STATEMENT_TAGS:
            names.append(name)
    return sorted(set(names))


def load_filing_candidates() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    statement: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    markdown_root = PROJECT_ROOT / "markdown_pages"
    for review_path in sorted((PROJECT_ROOT / "review").glob("*/*/*.jsonl")):
        try:
            lines = review_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            relative = compact(record.get("relative_path"), 500)
            source = markdown_root / relative
            if not relative or not source.is_file():
                continue
            parts = Path(relative).parts
            if len(parts) < 4:
                continue
            ticker, year, document = parts[0], parts[1], parts[2]
            tags = statement_names(record)
            item = {
                "cohort": "filing_statement" if tags else "filing_other",
                "source_path": source.relative_to(PROJECT_ROOT).as_posix(),
                "page_key": f"filing:{relative}",
                "trusted_metadata": {
                    "corpus": "filing",
                    "collection": filing_cadence(document),
                    "publisher": ticker,
                    "entity": ticker,
                    "document_id": document,
                    "document_period": year,
                    "page_number": page_number(source),
                },
                "current_search_text": compact(record.get("search_text"), 2_000),
                "sampling_tags": tags,
                "stratum": f"{filing_cadence(document)}:{tags[0] if tags else ticker}",
            }
            (statement if tags else other).append(item)
    return statement, other


def markdown_candidates(root_name: str, cohort: str) -> list[dict[str, Any]]:
    root = PROJECT_ROOT / root_name
    rows: list[dict[str, Any]] = []
    for source in sorted(root.rglob("*.md")):
        relative_under_root = source.relative_to(root)
        parts = relative_under_root.parts
        if root_name == "macro_markdown":
            if len(parts) < 3:
                continue
            source_doc, chapter = parts[0], parts[1]
            doc_type = (
                "economic_survey"
                if source_doc.startswith("economic_survey")
                else "budget"
                if source_doc.startswith("budget")
                else "sbp_report"
                if source_doc.startswith("sbp")
                else "macro"
            )
            item_cohort = "economic_survey" if doc_type == "economic_survey" else "macro_other"
            metadata = {
                "corpus": "macro",
                "collection": doc_type,
                "publisher": doc_type.replace("_", " "),
                "document_id": source_doc,
                "document_period": "",
                "chapter": chapter,
                "page_number": page_number(source),
            }
            stratum = f"{doc_type}:{chapter}"
        elif root_name == "announcement_markdown":
            if len(parts) < 3:
                continue
            ticker, year = parts[0], parts[1]
            item_cohort = "announcement"
            metadata = {
                "corpus": "announcement",
                "collection": "psx_notice",
                "publisher": "Pakistan Stock Exchange",
                "entity": ticker,
                "document_id": source.stem,
                "document_period": year,
                "page_number": None,
            }
            stratum = ticker
        else:
            if len(parts) < 6:
                continue
            country, publisher, collection, year, document = parts[:5]
            item_cohort = "authoritative"
            metadata = {
                "corpus": "authoritative",
                "collection": collection,
                "publisher": publisher,
                "country": country,
                "document_id": document,
                "document_period": year,
                "page_number": page_number(source),
            }
            stratum = f"{publisher}:{collection}"
        if item_cohort != cohort:
            continue
        rel = source.relative_to(PROJECT_ROOT).as_posix()
        rows.append({
            "cohort": item_cohort,
            "source_path": rel,
            "page_key": f"{metadata['corpus']}:{relative_under_root.as_posix()}",
            "trusted_metadata": metadata,
            "current_search_text": "",
            "sampling_tags": [],
            "stratum": stratum,
        })
    return rows


def round_robin_sample(
    rows: list[dict[str, Any]],
    size: int,
    rng: random.Random,
    key: Callable[[dict[str, Any]], str] = lambda item: str(item["stratum"]),
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    for values in groups.values():
        rng.shuffle(values)
    group_names = sorted(groups)
    rng.shuffle(group_names)
    selected: list[dict[str, Any]] = []
    while group_names and len(selected) < size:
        remaining = []
        for name in group_names:
            values = groups[name]
            if values and len(selected) < size:
                selected.append(values.pop())
            if values:
                remaining.append(name)
        group_names = remaining
    if len(selected) != size:
        raise RuntimeError(f"Could sample only {len(selected)} of {size} requested rows")
    return selected


def prepare_sample(resample: bool = False) -> list[dict[str, Any]]:
    if SAMPLE_PATH.exists() and not resample:
        return [
            json.loads(line)
            for line in SAMPLE_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    rng = random.Random(SEED)
    filing_statement, filing_other = load_filing_candidates()
    pools = {
        "filing_statement": filing_statement,
        "filing_other": filing_other,
        "economic_survey": markdown_candidates("macro_markdown", "economic_survey"),
        "macro_other": markdown_candidates("macro_markdown", "macro_other"),
        "announcement": markdown_candidates("announcement_markdown", "announcement"),
        "authoritative": markdown_candidates("authoritative_markdown", "authoritative"),
    }
    sample: list[dict[str, Any]] = []
    for cohort, quota in QUOTAS.items():
        candidates = pools[cohort]
        selected = round_robin_sample(candidates, quota, rng)
        sample.extend(selected)
    rng.shuffle(sample)
    for index, item in enumerate(sample, start=1):
        source = PROJECT_ROOT / item["source_path"]
        text = source.read_text(encoding="utf-8", errors="replace")
        item["sample_index"] = index
        item["artifact_id"] = artifact_id(item["source_path"])
        item["input_sha256"] = sha256_text(text)
        item["input_chars"] = len(text)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    PAGE_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    SAMPLE_PATH.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in sample),
        encoding="utf-8",
    )
    return sample


def bounded_string_list(value: Any, item_limit: int = 180) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        text
        for text in (compact(item, item_limit) for item in value[:MAX_LIST_VALUES])
        if text
    ]


def validate_output(payload: Any, markdown: str) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    errors: list[str] = []
    metrics = {"anchors": 0, "grounded_anchors": 0, "blocks": 0, "ungrounded_blocks": 0}
    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")

    quality = payload.get("parse_quality")
    if not isinstance(quality, dict):
        quality = {}
        errors.append("parse_quality_missing")
    status = compact(quality.get("status"), 32).casefold()
    if status not in ALLOWED_PARSE_STATUS:
        errors.append("parse_quality_status_invalid")
        status = "incomplete"

    search_text = compact(payload.get("search_text"), 1_200)
    if len(search_text.split()) > 140:
        errors.append("search_text_too_long")
        search_text = " ".join(search_text.split()[:140])

    normalized_markdown = normalized_text(markdown)
    blocks: list[dict[str, Any]] = []
    raw_blocks = payload.get("evidence_blocks")
    if not isinstance(raw_blocks, list):
        raw_blocks = []
        errors.append("evidence_blocks_invalid")
    for raw_block in raw_blocks[:MAX_BLOCKS]:
        if not isinstance(raw_block, dict):
            continue
        description = compact(raw_block.get("description"), 360)
        if not description:
            continue
        anchors = bounded_string_list(raw_block.get("anchors"), 280)[:MAX_ANCHORS_PER_BLOCK]
        grounded = [anchor for anchor in anchors if normalized_text(anchor) in normalized_markdown]
        metrics["anchors"] += len(anchors)
        metrics["grounded_anchors"] += len(grounded)
        if anchors and not grounded:
            metrics["ungrounded_blocks"] += 1
        shape = compact(raw_block.get("data_shape"), 40).casefold() or None
        if shape not in ALLOWED_DATA_SHAPES:
            shape = None
        blocks.append({
            "description": description,
            "anchors": anchors,
            "grounded_anchors": grounded,
            "entities": bounded_string_list(raw_block.get("entities"), 120),
            "periods": bounded_string_list(raw_block.get("periods"), 80),
            "qualifiers": bounded_string_list(raw_block.get("qualifiers"), 180),
            "units": bounded_string_list(raw_block.get("units"), 80),
            "data_shape": shape,
        })
    metrics["blocks"] = len(blocks)

    continuation = payload.get("continuation")
    if not isinstance(continuation, dict):
        continuation = {}
        errors.append("continuation_missing")
    normalized = {
        "parse_quality": {
            "status": status,
            "issues": bounded_string_list(quality.get("issues"), 220),
            "missing_regions": bounded_string_list(quality.get("missing_regions"), 220),
        },
        "search_text": search_text,
        "evidence_blocks": blocks,
        "continuation": {
            "continues_previous_page": continuation.get("continues_previous_page")
            if isinstance(continuation.get("continues_previous_page"), bool)
            else None,
            "continues_next_page": continuation.get("continues_next_page")
            if isinstance(continuation.get("continues_next_page"), bool)
            else None,
            "continuation_anchor": compact(continuation.get("continuation_anchor"), 280) or None,
        },
    }
    continuation_anchor = normalized["continuation"]["continuation_anchor"]
    if continuation_anchor and normalized_text(continuation_anchor) not in normalized_markdown:
        errors.append("continuation_anchor_not_grounded")
        normalized["continuation"]["continuation_anchor"] = None
    return normalized, errors, metrics


def output_path(item: dict[str, Any]) -> Path:
    return PAGE_OUTPUT_ROOT / f"{item['artifact_id']}.json"


def run_one(
    client: OpenAI,
    prompt: str,
    item: dict[str, Any],
    reset: bool,
) -> dict[str, Any]:
    target = output_path(item)
    if target.exists() and not reset:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if (
                existing.get("input_sha256") == item["input_sha256"]
                and existing.get("prompt_version") == PROMPT_VERSION
                and existing.get("model") == MODEL
            ):
                return existing
        except (OSError, json.JSONDecodeError):
            pass

    source = PROJECT_ROOT / item["source_path"]
    markdown = source.read_text(encoding="utf-8", errors="replace")
    truncated = len(markdown) > MAX_INPUT_CHARS
    body = markdown[:MAX_INPUT_CHARS]
    user_content = (
        f"{prompt}\n\n"
        "TRUSTED DOCUMENT METADATA\n"
        f"{json.dumps(item['trusted_metadata'], ensure_ascii=False, sort_keys=True)}\n\n"
        "PAGE MARKDOWN\n"
        f"{body}"
    )
    started = time.perf_counter()
    last_error = ""
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "Return grounded retrieval metadata for one page as strict JSON.",
                    },
                    {"role": "user", "content": user_content},
                ],
            )
            raw_content = response.choices[0].message.content or ""
            raw_payload = json.loads(raw_content)
            normalized, validation_errors, metrics = validate_output(raw_payload, body)
            usage = response.usage
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            cost = (
                input_tokens * PRICE_PER_MILLION["input"]
                + output_tokens * PRICE_PER_MILLION["output"]
            ) / 1_000_000
            artifact = {
                "schema_version": SCHEMA_VERSION,
                "prompt_version": PROMPT_VERSION,
                "prompt_sha256": sha256_text(prompt),
                "model": MODEL,
                "provider": "deepseek",
                "provider_request_id": getattr(response, "_request_id", None),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "page_key": item["page_key"],
                "source_path": item["source_path"],
                "cohort": item["cohort"],
                "trusted_metadata": item["trusted_metadata"],
                "sampling_tags": item["sampling_tags"],
                "current_search_text": item["current_search_text"],
                "input_sha256": item["input_sha256"],
                "input_chars": item["input_chars"],
                "input_truncated": truncated,
                "attempts": attempt,
                "latency_ms": round((time.perf_counter() - started) * 1_000),
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": round(cost, 8),
                },
                "validation_errors": validation_errors,
                "validation_metrics": metrics,
                "output": normalized,
                "raw_response": raw_payload,
                "status": "ok",
            }
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return artifact
        except Exception as exc:  # provider and content errors remain visible in the artifact
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
        "sampling_tags": item["sampling_tags"],
        "current_search_text": item["current_search_text"],
        "input_sha256": item["input_sha256"],
        "input_chars": item["input_chars"],
        "input_truncated": len(markdown) > MAX_INPUT_CHARS,
        "attempts": 3,
        "latency_ms": round((time.perf_counter() - started) * 1_000),
        "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0},
        "validation_errors": [],
        "validation_metrics": {},
        "output": None,
        "raw_response": None,
        "status": "error",
        "error": last_error,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def build_report(sample: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    by_cohort = Counter(item["cohort"] for item in sample)
    statuses = Counter(item.get("status", "missing") for item in artifacts)
    parse_statuses = Counter(
        item.get("output", {}).get("parse_quality", {}).get("status", "missing")
        for item in artifacts
        if item.get("output")
    )
    validation_errors = Counter(
        error
        for item in artifacts
        for error in item.get("validation_errors", [])
    )
    total_anchors = sum(item.get("validation_metrics", {}).get("anchors", 0) for item in artifacts)
    grounded_anchors = sum(
        item.get("validation_metrics", {}).get("grounded_anchors", 0)
        for item in artifacts
    )
    total_blocks = sum(item.get("validation_metrics", {}).get("blocks", 0) for item in artifacts)
    pages_with_blocks = sum(
        bool(item.get("output", {}).get("evidence_blocks"))
        for item in artifacts
        if item.get("output")
    )
    pages_with_search_text = sum(
        bool(item.get("output", {}).get("search_text"))
        for item in artifacts
        if item.get("output")
    )
    usage = {
        "input_tokens": sum(item.get("usage", {}).get("input_tokens", 0) for item in artifacts),
        "output_tokens": sum(item.get("usage", {}).get("output_tokens", 0) for item in artifacts),
        "cost_usd": round(sum(item.get("usage", {}).get("cost_usd", 0) for item in artifacts), 6),
    }
    latencies = sorted(
        int(item.get("latency_ms", 0))
        for item in artifacts
        if item.get("status") == "ok"
    )
    percentile = lambda fraction: latencies[min(len(latencies) - 1, int(len(latencies) * fraction))] if latencies else None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "seed": SEED,
        "sample_size": len(sample),
        "sample_by_cohort": dict(sorted(by_cohort.items())),
        "artifacts_found": len(artifacts),
        "statuses": dict(sorted(statuses.items())),
        "parse_statuses": dict(sorted(parse_statuses.items())),
        "pages_with_search_text": pages_with_search_text,
        "pages_with_evidence_blocks": pages_with_blocks,
        "evidence_blocks": total_blocks,
        "anchors": {
            "total": total_anchors,
            "grounded": grounded_anchors,
            "grounding_rate": round(grounded_anchors / total_anchors, 4) if total_anchors else None,
        },
        "validation_errors": dict(sorted(validation_errors.items())),
        "input_truncated_pages": sum(bool(item.get("input_truncated")) for item in artifacts),
        "retried_pages": sum(int(item.get("attempts", 1)) > 1 for item in artifacts),
        "usage": usage,
        "latency_ms": {
            "median": percentile(0.5),
            "p90": percentile(0.9),
            "max": latencies[-1] if latencies else None,
        },
        "sample_path": str(SAMPLE_PATH.relative_to(PROJECT_ROOT)),
        "page_artifact_root": str(PAGE_OUTPUT_ROOT.relative_to(PROJECT_ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Page Index V2 enrichment pilot")
    parser.add_argument("--sample-only", action="store_true")
    parser.add_argument("--resample", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    sample = prepare_sample(resample=args.resample)
    counts = Counter(item["cohort"] for item in sample)
    print(f"Prepared {len(sample):,} pages: {dict(sorted(counts.items()))}")
    if args.sample_only:
        return 0

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    active = sample[: args.limit] if args.limit else sample
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
        timeout=90,
        max_retries=0,
    )

    print(f"Enriching {len(active):,} pages with {MODEL} using {args.workers} workers")
    results: list[dict[str, Any]] = []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(run_one, client, prompt, item, args.reset): item
            for item in active
        }
        with tqdm(total=len(futures), desc="page-index-v2") as progress:
            for future in as_completed(futures):
                result = future.result()
                with lock:
                    results.append(result)
                progress.update(1)

    all_artifacts = []
    for item in sample:
        path = output_path(item)
        if not path.exists():
            continue
        try:
            all_artifacts.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    report = build_report(sample, all_artifacts)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if report["statuses"].get("error", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
