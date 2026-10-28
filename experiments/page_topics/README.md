# Page-topic experiment (parked)

A deterministic classifier that assigns a `page_topic` to every filing page, kept separate
from `section_tags` so the precision-tuned statement tags that feed extraction and QC stay
untouched.

**Status: parked.** The measurements below stand and motivated the search-guidance work now
shipped in `apps/context-builder`, but no tags were added to the pipeline or D1. Nothing here
writes to production — `build_index.py` builds a local SQLite index from `markdown_pages/`
and `review/`.

## Why it exists

BM25 over this corpus ranks contents and cover pages above real content for structural
queries, because those pages are short and repeat every statement name while BM25 divides by
document length. FTS5 hardcodes `k1=1.2, b=0.75` and exposes only column weights, so length
normalization cannot be tuned down.

## What was measured

7 tickers across banks, OMC, cement, power and fertiliser — 17,372 pages.

| | result |
|---|---|
| Topic coverage vs `section_tags` baseline | 19.7% → 68.5% |
| Navigation pollution, ticker-scoped structural queries | 52% → 0% of top 5 |
| Navigation pollution, specific-term queries | already 0–6%, unchanged |
| Urdu pages identified as their own class | 702 |
| Pages with no `search_text` at all (corpus-wide) | 18,905 (7.4%), of which 6,774 carry >2,000 chars |

The honest read: this only helps *structural* queries. Specific queries such as "borrowing
maturity profile" were already clean, which is why the shipped fix was to guide researchers
toward specific vocabulary rather than to add tags.

## Known issues if resumed

1. **Unfinished substring fix.** A word-boundary matcher was added after finding `vision`
   matching inside `provision`, tagging UBL's off-balance-sheet provisions page as a mission
   statement. The rebuild and re-measure after that fix were never run, so the ~6%
   over-exclusion figure is stale.
2. **`governance` and `narrative_review` should not be excluded wholesale.** A directors'
   report carries real figures (`gross revenue 279.4 billion net profit 43.5 billion eps
   134.4`), and the serving engine treats `"chairman review"` as high-signal. Split the
   non-content group into *always demote* (contents, cover, notices, Urdu) versus *rank
   lower but keep* (governance, narrative, sustainability).
3. **~31% still unclassified**, mostly note-continuation pages sharing one generic heading.

## Files

```
taxonomy.py     vocabulary + rules; every assignment carries the rule that produced it
build_index.py  local FTS5 index mirroring the worker's schema and bm25 weights
evaluate.py     pollution@5 before/after, needing no human relevance labels
```

```bash
.venv/bin/python experiments/page_topics/build_index.py
.venv/bin/python experiments/page_topics/evaluate.py
.venv/bin/python experiments/page_topics/evaluate.py --show "financial statements"
```
