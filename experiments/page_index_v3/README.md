# Compact Page Index — 20,000-record experiment

> **Retrieval evaluation invalidated.** The enrichment sidecars are preserved,
> but the current sample/evaluator cannot measure retrieval lift. Read
> `artifacts/reports/page_index_v3_experiment_audit.md` before using anything
> in this folder. Do not rerun `evaluate.py` as evidence of production quality.

This experiment tests a deliberately small BM25 Key representation over a
query-centred corpus. It is additive: source Markdown, current search fields,
and production D1 remain unchanged.

The compact retrieval key is not a user-facing summary. It is a bounded
search representation (maximum 110 words) that names the evidence actually
present on the page: relationships, actions, measurements, breakdowns, bases,
and comparison periods. Up to five plausible analyst search phrasings are a
separate field. The original Markdown remains the Value/raw-text lane, while a
short display summary is retained only for result cards.

The exact 20,000-record sample contains every local announcement, macro page,
and other authoritative page. The remaining slots are recent filing pages
selected against real production D1 query shapes from the most recent annual
and three most recent quarterly documents per ticker.

```bash
# Read production query shapes and build the deterministic sample.
.venv/bin/python experiments/page_index_v3/prepare_sample.py

# Generate or resume compact keys. Generation stops before exceeding the cap.
.venv/bin/python experiments/page_index_v3/run_enrichment.py \
  --workers 128 --budget-usd 17

# Invalidated prototype only; not a production-mirrored evaluation.
# .venv/bin/python experiments/page_index_v3/evaluate.py
```

The generation budget is $17 so at least $3 of the user's $20 experiment cap
remains available for independent retrieval judging.

Outputs:

- `artifacts/reports/page_index_v3_sample.json`
- `artifacts/reports/page_index_v3_enrichment.json`
- `artifacts/reports/page_index_v3_retrieval_eval.json`
- `artifacts/page_index_v3_20k/shadow_index.sqlite`
