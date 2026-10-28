# Page Index V2 pilot

This experiment tests whether one generalized, open-world page-enrichment pass
improves retrieval across heterogeneous corpora without replacing source
Markdown or forcing pages into a fixed taxonomy.

The 1,000-page sample deliberately oversamples minority collections:

| Cohort | Pages |
| --- | ---: |
| Filing pages carrying an existing statement tag | 160 |
| Other filing pages | 340 |
| Pakistan Economic Survey pages | 150 |
| Other macro pages (budget and SBP reports) | 100 |
| PSX announcements | 150 |
| Other authoritative publications | 100 |

Existing statement tags are used only to construct a diverse sample. They are
not treated as ground truth, sent as model instructions, or used by the shadow
ranker.

DeepSeek V4 Flash receives trusted document metadata and existing Markdown. It
returns a compact `search_text`, zero or more open-ended evidence blocks,
verbatim anchors, visible qualifiers, and page-continuation signals. Every
anchor is checked against the Markdown. Outputs are versioned sidecars under
`artifacts/page_index_v2_pilot/`; nothing writes to D1 or changes the current
search index.

```bash
# Create or inspect the deterministic sample without model calls.
.venv/bin/python experiments/page_index_v2/run_pilot.py --sample-only

# Run or resume the full enrichment pass.
.venv/bin/python experiments/page_index_v2/run_pilot.py --workers 64

# Build the shadow index and evaluate retrieval.
.venv/bin/python experiments/page_index_v2/evaluate.py

# Inspect ranked pages and the signals that caused them to surface.
.venv/bin/python experiments/page_index_v2/search_shadow.py \
  "agriculture contribution to GDP and growth" --corpus macro
```

The durable decision artifacts are:

- `artifacts/page_index_v2_pilot/sample.jsonl`
- `artifacts/page_index_v2_pilot/pages/*.json`
- `artifacts/reports/page_index_v2_pilot.json`
- `artifacts/page_index_v2_pilot/shadow_index.sqlite`
- `artifacts/reports/page_index_v2_retrieval_eval.json`
