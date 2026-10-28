# Generated Page Key Ablation — 2026-07-29

## Decision

The compact generated page Key is a useful retrieval representation, but the
current 20,000-page sidecar set must not be promoted as a global production
lane yet.

The experiment proves three narrower claims:

1. The Key signal is being searched and materially changes results.
2. It is strongest where relevant-page sidecar coverage is high, especially
   announcements and macro documents.
3. Equal-weight fusion with a selective filing Key index can improve ranked
   pages overall while still inserting noise into already-strong filing
   results and compiled context.

The safe next move is to build query-independent Key coverage for complete
documents or the full target corpus, then calibrate fusion on a development
set and validate it once on an untouched holdout. No query-specific search
rules should be added from this report.

## Frozen Boundary

- Queries: 50 distinct production-query shapes, known failures, and known
  collection shapes.
- Router: live `routeWithModel` output plus the same
  `materializeModelRoute` used by the browser.
- Retrieval: the same `executeRetrievalPlan` used by the UI.
- Context: the same `selectContextBundle` evidence-grid selection used by
  **Add best pages to context**.
- Comparators: unchanged production baseline, independent Key-only lane, and
  equal-weight baseline-plus-Key RRF.
- Evidence: original source Markdown, bounded at 18,000 characters per page.
- Judging: separate content-addressed Gemini 3.5 Flash Lite calls for route
  correctness and page relevance.
- Test infrastructure: isolated D1 `psxgpt-corpus-key-eval-20260729` and
  isolated Worker `psxgpt-corpus-api-key-eval`.
- Production D1, R2, and production search deployment: unchanged.

Immutable run:

`artifacts/corpus_browser_key_ablation/runs/2026-07-29T22-16-22.565Z/eval_report.json`

## Coverage

The 20,000 enrichment sample was not query-independent:

- all local announcement and macro candidates were included before the
  enrichment quality gate;
- every sampled filing page was selected by the existing D1 ranker;
- only 9,140 of 98,137 eligible recent filing pages were selected;
- 18,121 eligible sidecars mapped to exact production page IDs;
- 17 of 49 ranker-qualified cases had at least 80% Key coverage among the
  baseline top five; 32 did not.

Therefore:

- overall baseline-versus-combined metrics describe a valid partial rollout;
- Key-only filing metrics do not estimate a complete Key index;
- the high-coverage and non-filing slices are the clearest evidence about Key
  representation quality.

## Results

Forty-nine of 50 routes were ranker-qualified. One cross-bank query combined
deposit ranking and current valuation without decomposing the two evidence
needs or supplying a source for current valuation.

| Mode | nDCG@5 | P@5 | Direct page @1 | Excellent compiled context | Median latency | P90 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 0.574 | 0.442 | 34.7% | 38.8% | 521 ms | 1,529 ms |
| Key-only | 0.586 | 0.469 | 36.7% | 36.7% | 135 ms | 176 ms |
| Combined | 0.640 | 0.487 | 42.9% | 38.8% | 537 ms | 1,376 ms |

Combined beat baseline on nDCG@5 in 19 cases, tied in 18, and lost in 12.
The overall ranked-page lift is real, but compiled-context excellence was
flat and compiled-context precision fell slightly from 0.513 to 0.501.

### High baseline-candidate Key coverage

| Mode | Cases | nDCG@5 | Direct page @1 | Excellent compiled context |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 17 | 0.553 | 35.3% | 35.3% |
| Key-only | 17 | 0.735 | 52.9% | 70.6% |
| Combined | 17 | 0.669 | 47.1% | 52.9% |

### Non-filing source sample

| Mode | Cases | nDCG@5 | Direct page @1 | Excellent compiled context |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 19 | 0.494 | 31.6% | 31.6% |
| Key-only | 19 | 0.658 | 47.4% | 63.2% |
| Combined | 19 | 0.599 | 42.1% | 47.4% |

This is the strongest evidence that the generated representation itself is
good. It notably surfaced direct dividend, insider-transaction, inflation,
energy-capacity, and acquisition pages that the existing summary/text lanes
ranked behind generic notices or chapter pages.

## Manual Movement Audit

Material gains included:

- MEBL dividend notices: Key-only returned five direct dividend/entitlement
  notices while baseline led with board-meeting and AGM notices.
- Economic Survey energy: generated Keys surfaced installed-capacity and
  generation-mix pages directly.
- Economic Survey inflation: generated Keys surfaced specific inflation and
  food-driver evidence instead of relying on generic chapter matches.
- AGP imported APIs: generated Keys surfaced the API-development and import
  dependence evidence absent from baseline's first pages.
- NRL segment query: the Key lane moved the lube-base-oil segment page to the
  first position.

Material regressions were concentrated where the correct filing pages were not
tagged:

- HBL and UBL latest cash balances;
- Askari General operating cash flow and retained profits;
- Systems regional revenue;
- multi-page capacity and financial-model requests.

In these cases equal-weight RRF inserted partial-Key noise between direct
baseline pages. The correct structural response is broader, query-independent
coverage and calibrated fusion—not cash, company, statement, or query-specific
rules.

## Router Boundary Fixes

The run exposed two contradictory validation rules:

1. The prompt required an entity `mention` to quote the user, while validation
   required that quote to be a ticker or legal-name substring.
2. The model could mark a user phrase unresolved even when it uniquely matched
   the directory it received.

The corrected contract:

- ticker identity must exist in the directory;
- mention evidence must be an exact normalized span from the current or prior
  user request;
- a uniquely directory-matched unresolved span invalidates the plan and goes
  through the existing model corrective retry;
- application code never repairs, aliases, or substitutes the entity itself.

Focused replays for `NRLs`, `engro holdings`, and `Rafhan Maize` all passed
after the contract correction.

## What Was Not Added

- no regex router;
- no query-specific synonyms;
- no statement pack;
- no search fallback;
- no corpus-specific rescue rule;
- no eval-driven lane-weight change;
- no production data mutation;
- no production search deployment.
