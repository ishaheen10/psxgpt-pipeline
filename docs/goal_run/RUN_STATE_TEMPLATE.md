# Goal Run State

Use this file at the start of a long-running goal. Copy it to
`artifacts/reports/goal_run_<YYYY-MM-DD>.md` and keep it current as the run
progresses. Do not rely on chat history as the only state store.

## Objective

- User objective:
- Current narrowed loop:
- Start time:
- Operator:

## Non-Negotiables

- P/E = latest `market_cap / latest annual_or_LTM net_profit`.
- Do not use EPS or historical shares for P/E.
- SCS is an external reference check only, not a source of truth.
- Do not let SCS choose the production denominator.
- AGP is a known source-quality caveat unless new source evidence says otherwise.
- Fix the earliest bad artifact; do not hand-patch downstream rows.
- Production changes require an explicit D1/app update record.

## Current Hypothesis

- Suspected root cause:
- Earliest pipeline stage implicated:
- Evidence that supports it:
- Evidence that would disprove it:

## Current Issue Classification

Choose exactly one primary category before editing code:

- `extraction_or_arithmetic_defect`
- `source_report_caveat`
- `stale_or_missing_market_data`
- `scs_reference_basis_drift`
- `d1_publish_or_artifact_sync`
- `serve_retrieval_or_reasoning_failure`
- `out_of_universe_or_user_scope`
- `transient_provider_or_runtime`

Primary category:

Why this category:

Why the other likely categories were rejected:

## Evidence Ledger

| Time | Source | Command/File/Endpoint | Finding | Decision |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

## Before / After

| Metric or row | Before | After | Evidence |
| --- | ---: | ---: | --- |
|  |  |  |  |

## Commands Run

```bash
# Paste exact commands here.
```

## D1 / Production Changes

| Time | Target | Command | Rows/Deploy URL | Verification |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

## Current Health Snapshot

- Pipeline state:
- Production matrix:
- Admin issues:
- Eval or replay status:

## Open Items

| Item | Category | Owner | Next command/evidence | Blocking? |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

## Next Three Actions

1.
2.
3.

## Handoff

- What is fixed:
- What is not fixed:
- Residual risk:
- Resume command:

