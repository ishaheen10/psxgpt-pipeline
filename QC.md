# psxGPT Pipeline QC Snapshot

Generated from the current Step 19 state gate on 2026-06-23.

## Current State

- Tickers checked: 471
- D1 coverage: 471 tickers each for statements, documents, ratios, and market data
- Blocking tickers: 1
- Warning tickers: 23
- Open extraction QC failures: 3 failures across 1 ticker
- Serve-consumable quality flags: 46 total
  - 3 blocker flags
  - 43 warning flags
  - 15 financial statement flags
  - 31 ratio flags

The current red blocker is `AGP`, specifically the annual 2024 unconsolidated cash
flow statement from source page 114. Step 19 points this back to `09_qc_cf`, with
the earliest bad stage as `08_extract`.

## Red Blocker

| Ticker | Filing | Statement | Section | Failing metrics | Current rerun path |
| --- | --- | --- | --- | --- | --- |
| AGP | annual 2024 | cash flow | unconsolidated | `cfo`, `cfi`, `cff` subtotal reconciliation | `python3 pipeline/14_triage.py` |

The AGP page is image-based and messy. When repairing it, stay faithful to the
PDF-reported values. If visible child rows do not reconcile to a printed subtotal
and visual inspection supports a source-filing inconsistency or omitted line,
classify it as `source_report_inconsistent_subtotal` with warning severity and a
deterministic `user_caution_json` payload. Do not fabricate balancing rows.

## Yellow Warnings

Warnings are query-scoped cautions, not ticker blacklists. They should only
surface when the answer retrieves or depends on the flagged slice:

- `financial_statements` warnings: source filing subtotals that appear internally
  inconsistent or have omitted/unlabelled lines.
- `ratios` warnings: ratio outliers where denominator scale or reference checks
  require caution.

Current examples include `NATF`, `MTL`, `RPL`, `TPLP`, `UNITY`, and `ZAHID` cash
flow subtotal cautions. These should tell users that psxGPT keeps the
PDF-reported subtotal and recommends inspecting the linked source page when the
specific flagged data is used.

## Artifacts To Inspect

- `artifacts/state/pipeline_gate.md`
- `artifacts/state/open_error_queue.jsonl`
- `artifacts/quality/data_quality_flags.md`
- `artifacts/quality/data_quality_flags.jsonl`
- `artifacts/reports/19_pipeline_state.json`

## Acceptance Gate

Before calling the corpus healthy:

1. Run `python3 run.py --chain state`.
2. Confirm `blocking_tickers` is zero or every blocker has an explicit accepted
   source-filing warning rationale.
3. If flags changed, run `python3 pipeline/13_d1_push.py --quality-flags`.
4. In `psxgpt-serve`, confirm the admin data-health matrix shows the expected
   green/yellow/red cells and that a query touching a flagged slice surfaces the
   deterministic caution text.
