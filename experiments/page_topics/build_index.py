#!/usr/bin/env python3
"""Builds a local FTS5 index that mirrors production, then applies page topics.

The schema, the column order, and the bm25 column weights match
`apps/corpus-browser/api/worker.ts` so that ranking behaviour here is
comparable to the deployed search rather than merely similar.

Nothing is written to D1. This exists to measure whether a deterministic topic
tag improves retrieval before any production change is proposed.

Usage:
    .venv/bin/python experiments/page_topics/build_index.py --tickers UBL,PSO
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from taxonomy import classify  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_ROOT = PROJECT_ROOT / "markdown_pages"
REVIEW_ROOT = PROJECT_ROOT / "review"
INDEX_PATH = Path(__file__).resolve().parent / "corpus.db"

DEFAULT_TICKERS = ["UBL", "MCB", "PSO", "LUCK", "HUBC", "ENGRO", "FFC"]

SCHEMA = """
DROP TABLE IF EXISTS pages;
DROP TABLE IF EXISTS pages_fts;

CREATE TABLE pages (
    id            INTEGER PRIMARY KEY,
    ticker        TEXT NOT NULL,
    filing        TEXT NOT NULL,
    filing_type   TEXT NOT NULL,
    year          INTEGER,
    pg            INTEGER,
    section_tags  TEXT,
    search_text   TEXT,
    text          TEXT,
    page_topic    TEXT,
    topic_conf    REAL,
    topic_rule    TEXT
);

-- Column order matches filings_fts_new: (search_text, text).
CREATE VIRTUAL TABLE pages_fts USING fts5(
    search_text,
    text,
    content='pages',
    content_rowid='id'
);
"""


def filing_meta(filing: str) -> tuple[str, int | None]:
    lowered = filing.lower()
    filing_type = "quarterly" if "quarterly" in lowered else "annual"
    year = None
    for token in filing.replace("-", "_").split("_"):
        if len(token) == 4 and token.isdigit():
            year = int(token)
            break
    return filing_type, year


def load_rows(tickers: list[str]) -> list[dict]:
    rows: list[dict] = []
    for ticker in tickers:
        review_dir = REVIEW_ROOT / ticker
        if not review_dir.exists():
            print(f"  {ticker}: no review/ directory, skipped")
            continue
        for jsonl in sorted(review_dir.glob("*/*.jsonl")):
            filing = jsonl.stem
            filing_type, year = filing_meta(filing)
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rel = record.get("relative_path") or ""
                md_path = MARKDOWN_ROOT / rel
                if not md_path.exists():
                    continue
                text = md_path.read_text(encoding="utf-8", errors="replace")
                digits = "".join(c for c in md_path.stem if c.isdigit())
                rows.append(
                    {
                        "ticker": ticker,
                        "filing": filing,
                        "filing_type": filing_type,
                        "year": year,
                        "pg": int(digits) if digits else 0,
                        "section_tags": record.get("section_tags") or [],
                        "search_text": record.get("search_text") or "",
                        "text": text,
                    }
                )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the local page-topic experiment index")
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    args = parser.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    started = time.time()
    print(f"Loading pages for {', '.join(tickers)} …")
    rows = load_rows(tickers)
    if not rows:
        print("No pages loaded. Check markdown_pages/ and review/.")
        return 1
    print(f"  {len(rows):,} pages loaded in {time.time() - started:.1f}s")

    print("Classifying page topics …")
    counts: dict[str, int] = {}
    for row in rows:
        tags = row["section_tags"] if isinstance(row["section_tags"], list) else []
        result = classify(row["text"], row["pg"], tags, row["search_text"])
        row["page_topic"] = result.topic
        row["topic_conf"] = result.confidence
        row["topic_rule"] = result.rule
        counts[result.topic] = counts.get(result.topic, 0) + 1

    INDEX_PATH.unlink(missing_ok=True)
    connection = sqlite3.connect(INDEX_PATH)
    connection.executescript(SCHEMA)
    connection.executemany(
        """INSERT INTO pages
           (ticker, filing, filing_type, year, pg, section_tags, search_text, text,
            page_topic, topic_conf, topic_rule)
           VALUES (:ticker, :filing, :filing_type, :year, :pg, :section_tags_json,
                   :search_text, :text, :page_topic, :topic_conf, :topic_rule)""",
        [{**row, "section_tags_json": json.dumps(row["section_tags"])} for row in rows],
    )
    connection.execute(
        "INSERT INTO pages_fts(rowid, search_text, text) SELECT id, search_text, text FROM pages"
    )
    connection.commit()

    total = len(rows)
    print(f"\nIndexed {total:,} pages into {INDEX_PATH.relative_to(PROJECT_ROOT)}\n")
    print(f"{'topic':<28}{'pages':>8}{'share':>9}")
    print("-" * 45)
    for topic, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{topic:<28}{count:>8,}{100 * count / total:>8.1f}%")

    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
