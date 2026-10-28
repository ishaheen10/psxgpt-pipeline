# Issue Output Contract

Long-running repair goals should not emit only counts or ticker lists. Every
pipeline issue artifact that can drive repairs should make the next rerun loop
obvious.

## Required Fields For Repairable Issues

Each row in a repair-driving artifact should include:

- `ticker`
- `stage`
- `origin_stage`
- `category`
- `period_end`
- `period_duration`
- `statement_type`
- `section`
- `metric` or `canonical_name`
- `source_file`
- `source_pages`
- `source_url`, when available
- `expected`
- `got`
- `rule`
- `recommended_command`
- `user_visible`
- `severity`
- `why_this_category`

## Categories

Use one primary category:

- `extraction_or_arithmetic_defect`
- `source_report_caveat`
- `stale_or_missing_market_data`
- `scs_reference_basis_drift`
- `d1_publish_or_artifact_sync`
- `serve_retrieval_or_reasoning_failure`
- `out_of_universe_or_user_scope`
- `transient_provider_or_runtime`

## Good Issue Message Shape

Good:

```text
Step 11 derive produced a scale discontinuity for FFC 2026-03-31 consolidated
profit_loss net_profit LTM. Source components use mixed unit labels; rerun
python3 pipeline/11_derive.py --type pl --ticker FFC after unit normalization
fix, then flatten PL and rebuild ratios.
```

Bad:

```text
FFC ratio outlier.
```

## Before / After Requirement

Every repair report should include a before/after table:

| Ticker | Period | Field | Before | After | Evidence |
| --- | --- | --- | ---: | ---: | --- |
| FFC | 2026-03-31 | net_profit LTM | 87,274.824 | 87,274,824 | D1 spot check |

## Production Update Requirement

If a fix changes production data or behavior, the report must name the exact
target:

- D1 table pushed
- row count
- ratios rebuilt or not
- quality flags pushed or not
- backend Worker deployed or not
- Pages deployed or not
- production endpoint used to verify

