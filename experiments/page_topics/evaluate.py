#!/usr/bin/env python3
"""Measures whether page topics improve retrieval.

Baseline mirrors the deployed worker exactly: the same FTS5 table shape, the
same `bm25(fts, 1.4, 1.0)` column weights, the same all-terms-then-any-terms
fallback. The only difference between arms is what the topic column is allowed
to do.

The headline metric is deliberately one that needs no human relevance labels:

    pollution@5 — the share of the top 5 results that are navigation or
    front/back matter (contents, cover, governance boilerplate, notices,
    Urdu sections). A page in that class is essentially never the answer to
    an analyst question, so any of them in the top 5 is a wasted slot.

Usage:
    .venv/bin/python experiments/page_topics/evaluate.py
    .venv/bin/python experiments/page_topics/evaluate.py --show HUBC
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from taxonomy import NON_CONTENT  # noqa: E402

INDEX_PATH = Path(__file__).resolve().parent / "corpus.db"
TOP_K = 5

# Realistic analyst questions. `expected` is the topic a researcher would pick
# from a page-type filter; None means the query has no single obvious type.
QUERIES: list[tuple[str, str | None]] = [
    ("financial statements", None),
    ("annual report balance sheet", None),
    ("interest rate risk exposure", "financial_risk"),
    ("borrowing maturity profile", "debt_and_borrowings"),
    ("related party transactions", "related_party"),
    ("segment information profit", "segment_information"),
    ("pension gratuity actuarial assumptions", "employee_benefits"),
    ("deferred tax", "taxation"),
    ("contingencies guarantees letters of credit", "contingencies_commitments"),
    ("capital adequacy tier 1", "capital_management"),
    ("earnings per share dividend", "dividends_and_eps"),
    ("trade debts receivables", "receivables_payables"),
    ("property plant equipment depreciation", "ppe_and_assets"),
    ("credit risk concentration", "financial_risk"),
]

BASE_SQL = """
SELECT p.ticker, p.pg, p.filing, p.page_topic, p.topic_conf,
       substr(p.search_text, 1, 88) AS summary,
       bm25(pages_fts, 1.4, 1.0) AS score
FROM pages_fts
JOIN pages p ON p.id = pages_fts.rowid
WHERE pages_fts MATCH ?
"""


def fts_queries(text: str) -> tuple[str, str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", text)[:16]
    quoted = [f'"{t}"' for t in tokens]
    return " AND ".join(quoted), " OR ".join(quoted)


def run(db: sqlite3.Connection, query: str, where: str = "", limit: int = TOP_K) -> list[tuple]:
    """All-terms first, falling back to any-term, exactly as the worker does."""
    all_q, any_q = fts_queries(query)
    sql = f"{BASE_SQL} {where} ORDER BY score LIMIT {limit}"
    rows = db.execute(sql, (all_q,)).fetchall()
    if not rows and any_q != all_q:
        rows = db.execute(sql, (any_q,)).fetchall()
    return rows


def pollution(rows: list[tuple]) -> float:
    if not rows:
        return 0.0
    bad = sum(1 for r in rows if r[3] in NON_CONTENT)
    return bad / len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", help="print the top results for queries matching this string")
    args = parser.parse_args()

    if not INDEX_PATH.exists():
        print("corpus.db missing — run build_index.py first")
        return 1
    db = sqlite3.connect(INDEX_PATH)

    non_content_list = ",".join(f"'{t}'" for t in sorted(NON_CONTENT))
    exclude_where = f"AND p.page_topic NOT IN ({non_content_list})"

    print(f"{'query':<44}{'base':>7}{'-nav':>7}{'+topic':>8}  {'expected topic':<26}")
    print("-" * 96)

    base_tot = filt_tot = 0.0
    topic_hit_base = topic_hit_filt = 0
    topic_queries = 0

    for query, expected in QUERIES:
        base = run(db, query)
        filtered = run(db, query, exclude_where)

        p_base = pollution(base)
        p_filt = pollution(filtered)
        base_tot += p_base
        filt_tot += p_filt

        topic_cell = "—"
        if expected:
            topic_queries += 1
            where = f"AND p.page_topic = '{expected}'"
            scoped = run(db, query, where)
            # Did the expected topic already surface without help?
            hit_base = sum(1 for r in base if r[3] == expected)
            hit_filt = sum(1 for r in filtered if r[3] == expected)
            topic_hit_base += hit_base
            topic_hit_filt += hit_filt
            topic_cell = f"{len(scoped)}/{TOP_K}"

        print(
            f"{query[:43]:<44}{p_base:>6.0%}{p_filt:>7.0%}{topic_cell:>8}  "
            f"{str(expected or ''):<26}"
        )

    n = len(QUERIES)
    print("-" * 96)
    print(f"{'MEAN pollution@5':<44}{base_tot / n:>6.0%}{filt_tot / n:>7.0%}")
    if topic_queries:
        denom = topic_queries * TOP_K
        print(
            f"{'expected-topic pages in top 5':<44}"
            f"{topic_hit_base / denom:>6.0%}{topic_hit_filt / denom:>7.0%}"
        )

    if args.show:
        for query, expected in QUERIES:
            if args.show.lower() not in query.lower():
                continue
            for label, where in (("BASELINE", ""), ("EXCLUDE NAV", exclude_where)):
                print(f"\n--- {label}: {query}")
                for ticker, pg, filing, topic, conf, summary, score in run(db, query, where):
                    flag = "NAV" if topic in NON_CONTENT else "   "
                    print(f"  {flag} {ticker:<6} pg={pg:<4} {topic:<24} {(summary or '')[:60]}")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
