#!/usr/bin/env python3
"""Build the exact 20,000-record compact-key experiment manifest."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOCAL_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOCAL_PROJECT_ROOT))

from experiments.page_index_v2.run_pilot import (
    PROJECT_ROOT,
    load_filing_candidates,
    markdown_candidates,
    sha256_text,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "page_index_v3_20k"
QUERY_PATH = OUTPUT_ROOT / "queries.jsonl"
SAMPLE_PATH = OUTPUT_ROOT / "sample.jsonl"
SELECTION_DB_PATH = OUTPUT_ROOT / "selection_index.sqlite"
REPORT_PATH = PROJECT_ROOT / "artifacts" / "reports" / "page_index_v3_sample.json"

TARGET_SIZE = 20_000
SEED = 20260729
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "give", "has", "have", "how", "i", "in", "is", "it", "me",
    "of", "on", "or", "show", "the", "this", "to", "was", "were", "what",
    "which", "with", "year", "years",
}


def compact(value: Any, limit: int = 2_000) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def query_tokens(value: str, limit: int = 18) -> list[str]:
    found = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value.casefold())
    result = []
    for token in found:
        token = token.strip("./-_")
        if len(token) < 2 or token in STOP_WORDS or token in result:
            continue
        result.append(token)
        if len(result) >= limit:
            break
    return result


def export_queries() -> list[dict[str, Any]]:
    sql = """
    SELECT query, MAX(created_at) AS latest_at, COUNT(*) AS uses
    FROM query_logs
    WHERE query IS NOT NULL AND trim(query) <> ''
    GROUP BY lower(trim(query))
    ORDER BY latest_at DESC
    """
    command = [
        "pnpm", "--dir", "apps/corpus-browser", "exec", "wrangler",
        "d1", "execute", "psx", "--remote", "--json", "--command", sql,
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    rows = payload[0].get("results") or []
    queries = []
    for row in rows:
        text = compact(row.get("query"))
        if len(query_tokens(text)) < 2:
            continue
        queries.append({
            "query_id": hashlib.sha256(text.casefold().encode("utf-8")).hexdigest()[:16],
            "query_text": text,
            "latest_at": row.get("latest_at"),
            "uses": int(row.get("uses") or 1),
            "tokens": query_tokens(text),
        })
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    QUERY_PATH.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queries),
        encoding="utf-8",
    )
    return queries


def document_date(item: dict[str, Any]) -> str:
    document = str(item["trusted_metadata"].get("document_id") or "")
    quarterly = re.search(r"_Quarterly_(\d{4}-\d{2}-\d{2})", document)
    if quarterly:
        return quarterly.group(1)
    annual = re.search(r"_Annual_(\d{4})", document)
    if annual:
        return f"{annual.group(1)}-12-31"
    return ""


def recent_filing_pool() -> list[dict[str, Any]]:
    statement, other = load_filing_candidates()
    all_rows = statement + other
    documents: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        metadata = row["trusted_metadata"]
        documents[(metadata["entity"], metadata["document_id"])].append(row)

    selected_documents = set()
    by_ticker: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for ticker, document_id in documents:
        row = documents[(ticker, document_id)][0]
        by_ticker[ticker].append((
            row["trusted_metadata"]["collection"],
            document_date(row),
            document_id,
        ))
    for ticker, values in by_ticker.items():
        annuals = sorted((value for value in values if value[0] == "annual"), reverse=True)
        quarters = sorted((value for value in values if value[0] == "quarterly"), reverse=True)
        if annuals:
            selected_documents.add((ticker, annuals[0][2]))
        for value in quarters[:3]:
            selected_documents.add((ticker, value[2]))

    return [
        row
        for row in all_rows
        if (
            row["trusted_metadata"]["entity"],
            row["trusted_metadata"]["document_id"],
        ) in selected_documents
    ]


def build_selection_index(rows: list[dict[str, Any]]) -> sqlite3.Connection:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(SELECTION_DB_PATH)
    db.executescript(
        """
        DROP TABLE IF EXISTS docs;
        DROP TABLE IF EXISTS filing_fts;
        CREATE TABLE docs (
          id INTEGER PRIMARY KEY,
          source_path TEXT NOT NULL UNIQUE,
          ticker TEXT NOT NULL,
          cadence TEXT NOT NULL,
          document_id TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE filing_fts USING fts5(metadata, search_key, content);
        """
    )
    for rowid, row in enumerate(rows, start=1):
        metadata = row["trusted_metadata"]
        markdown = (PROJECT_ROOT / row["source_path"]).read_text(
            encoding="utf-8", errors="replace"
        )
        meta_text = " ".join([
            str(metadata.get("entity") or ""),
            str(metadata.get("publisher") or ""),
            str(metadata.get("collection") or ""),
            str(metadata.get("document_id") or "").replace("_", " "),
            str(metadata.get("document_period") or ""),
            " ".join(row.get("sampling_tags") or []),
        ])
        db.execute(
            "INSERT INTO docs VALUES (?, ?, ?, ?, ?)",
            (
                rowid,
                row["source_path"],
                metadata["entity"],
                metadata["collection"],
                metadata["document_id"],
            ),
        )
        db.execute(
            "INSERT INTO filing_fts(rowid, metadata, search_key, content) VALUES (?, ?, ?, ?)",
            (rowid, meta_text, row.get("current_search_text") or "", markdown),
        )
    db.commit()
    return db


def query_candidates(
    db: sqlite3.Connection,
    query: dict[str, Any],
    limit: int = 20,
) -> list[str]:
    tokens = query["tokens"]
    if not tokens:
        return []
    expression = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
    rows = db.execute(
        """
        SELECT docs.source_path
        FROM filing_fts
        JOIN docs ON docs.id = filing_fts.rowid
        WHERE filing_fts MATCH ?
        ORDER BY bm25(filing_fts, 4.0, 7.0, 0.5)
        LIMIT ?
        """,
        (expression, limit),
    ).fetchall()
    return [row[0] for row in rows]


def select_filing_pages(
    rows: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    target: int,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    db = build_selection_index(rows)
    candidate_lists = []
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for query in queries:
        paths = query_candidates(db, query)
        candidate_lists.append((query["query_id"], paths))
        for rank, source_path in enumerate(paths, start=1):
            if len(evidence[source_path]) < 12:
                evidence[source_path].append({"query_id": query["query_id"], "rank": rank})

    selected_paths = []
    seen = set()
    for rank_index in range(20):
        for _, paths in candidate_lists:
            if rank_index >= len(paths):
                continue
            path = paths[rank_index]
            if path in seen:
                continue
            selected_paths.append(path)
            seen.add(path)
            if len(selected_paths) == target:
                break
        if len(selected_paths) == target:
            break

    if len(selected_paths) < target:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row["source_path"] in seen:
                continue
            metadata = row["trusted_metadata"]
            groups[f"{metadata['entity']}:{metadata['collection']}"].append(row)
        for values in groups.values():
            values.sort(key=lambda row: (document_date(row), row["source_path"]), reverse=True)
        names = sorted(groups)
        while names and len(selected_paths) < target:
            remaining = []
            for name in names:
                values = groups[name]
                if values and len(selected_paths) < target:
                    row = values.pop(0)
                    selected_paths.append(row["source_path"])
                    seen.add(row["source_path"])
                if values:
                    remaining.append(name)
            names = remaining

    by_path = {row["source_path"]: row for row in rows}
    return [by_path[path] for path in selected_paths], evidence


def prepare_item(
    item: dict[str, Any],
    sample_index: int,
    selection_reason: str,
    selection_queries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source = PROJECT_ROOT / item["source_path"]
    markdown = source.read_text(encoding="utf-8", errors="replace")
    return {
        **item,
        "sample_index": sample_index,
        "artifact_id": sha256_text(item["source_path"])[:20],
        "input_sha256": sha256_text(markdown),
        "input_chars": len(markdown),
        "selection_reason": selection_reason,
        "selection_queries": selection_queries or [],
    }


def main() -> int:
    queries = export_queries()
    announcements = markdown_candidates("announcement_markdown", "announcement")
    economic_survey = markdown_candidates("macro_markdown", "economic_survey")
    macro_other = markdown_candidates("macro_markdown", "macro_other")
    authoritative = markdown_candidates("authoritative_markdown", "authoritative")
    nonfiling = announcements + economic_survey + macro_other + authoritative
    filing_target = TARGET_SIZE - len(nonfiling)
    if filing_target <= 0:
        raise SystemExit("Non-filing corpora exceed the 20,000-record target")

    filing_pool = recent_filing_pool()
    filings, selection_evidence = select_filing_pages(
        filing_pool, queries, filing_target
    )
    sample = []
    for item in nonfiling:
        sample.append(prepare_item(item, len(sample) + 1, "complete_corpus"))
    for item in filings:
        query_hits = selection_evidence.get(item["source_path"], [])
        sample.append(prepare_item(
            item,
            len(sample) + 1,
            "d1_query_candidate" if query_hits else "stratified_fill",
            query_hits,
        ))
    if len(sample) != TARGET_SIZE:
        raise RuntimeError(f"Expected {TARGET_SIZE}, built {len(sample)}")

    SAMPLE_PATH.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in sample),
        encoding="utf-8",
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "target_size": TARGET_SIZE,
        "queries_exported": len(queries),
        "recent_filing_pool": len(filing_pool),
        "sample_size": len(sample),
        "sample_by_cohort": dict(sorted(Counter(row["cohort"] for row in sample).items())),
        "selection_reasons": dict(sorted(Counter(
            row["selection_reason"] for row in sample
        ).items())),
        "filing_documents": len({
            row["trusted_metadata"]["document_id"]
            for row in sample
            if row["cohort"].startswith("filing_")
        }),
        "filing_tickers": len({
            row["trusted_metadata"]["entity"]
            for row in sample
            if row["cohort"].startswith("filing_")
        }),
        "sample_path": str(SAMPLE_PATH.relative_to(PROJECT_ROOT)),
        "query_path": str(QUERY_PATH.relative_to(PROJECT_ROOT)),
        "selection_index": str(SELECTION_DB_PATH.relative_to(PROJECT_ROOT)),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
