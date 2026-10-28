# Router × Page Representation Factorial — 2026-07-29

## Decision

The generated page Key is a modestly useful additive retrieval lane on the
same 20,000 pages. The current router revision is not a clean end-to-end
improvement because it retrieves more answer-bearing pages in the first five
while making the first result materially less reliable.

Do not discard the generated Keys, and do not expand the enrichment pass yet.
First fix and evaluate the router's typed scope decisions—especially filing
cadence, grouped entities, and comparison decomposition—then calibrate rank
fusion for first-page quality.

No query-specific retrieval rules should be added from this report.

## Accepted Boundary

Accepted immutable run:

`artifacts/page_index_v3_factorial/runs/2026-07-29T23-13-48.627977Z`

- Candidate universe: exactly the same 20,000 local records in every cell.
- Old router: commit `e094ca83b26c185448590a733eb15e7c0fe7f4e3`.
- New router: commit `944aff9ac3394e8f565f5d2f5527a0a8c0ea1dc8`.
- Old representation: trusted metadata, existing `search_text` when present,
  and original Markdown.
- New representation: the old representation plus a separate generated-Key
  lane over `retrieval_key` and `retrieval_phrases`.
- Retrieval execution: lexical, summary, text, heading, and generated-Key
  searches run independently and are fused with equal-weight RRF.
- Filters: routed corpus, entity/industry, year, and cadence are applied.
  Macro searches intentionally ignore company-industry filters, matching the
  Worker boundary.
- Entity directory: 478 production corpus entities, loaded successfully and
  treated as a hard evaluation dependency.
- Judgment: Gemini 3.5 Flash Lite scores source Markdown with router,
  representation, and rank hidden.
- nDCG: every cell is compared against one shared ideal ranking from the
  union of the four observed top-five result sets for that query.

The HMB 2024 annual-deposits query has no matching page in the fixed sample
and returns no result in any cell. It is retained in the 50-query headline
because it is a real scope limitation; the common-availability slice contains
49 cases.

## Results

### All 50 cases

| Cell | nDCG@5 | Answer-bearing P@5 | Direct page @1 | Excellent page @1 |
| --- | ---: | ---: | ---: | ---: |
| Old router + old representation | 0.549 | 46.8% | 76% | 60% |
| Old router + new representation | **0.565** | 50.0% | 74% | **60%** |
| New router + old representation | 0.538 | 51.6% | 64% | 54% |
| New router + new representation | 0.560 | **54.4%** | 68% | 56% |

### Common-availability slice

| Cell | nDCG@5 | Answer-bearing P@5 | Direct page @1 | Excellent page @1 |
| --- | ---: | ---: | ---: | ---: |
| Old router + old representation | 0.560 | 47.8% | 77.6% | 61.2% |
| Old router + new representation | **0.577** | 51.0% | 75.5% | **61.2%** |
| New router + old representation | 0.549 | 52.7% | 65.3% | 55.1% |
| New router + new representation | 0.571 | **55.5%** | 69.4% | 57.1% |

## What Moved the Needle

### Generated page representation

Holding the old router fixed:

- nDCG@5: `+0.016`
- answer-bearing P@5: `+3.2pp`
- direct page @1: `-2pp`
- 17 wins / 22 ties / 11 losses

Holding the new router fixed:

- nDCG@5: `+0.022`
- answer-bearing P@5: `+2.8pp`
- direct page @1: `+4pp`
- 22 wins / 15 ties / 13 losses

This establishes a real but modest retrieval contribution. The Keys work
better with the new router because it supplies an explicit compact-Key query.
They do not justify a full-corpus enrichment pass yet: the gain is uneven,
and the Key lane still displaces strong baseline pages in 13 new-router cases.

Material Key gains included:

- ABL versus MCB sector-wise advances: the Key lane promoted an answer-bearing
  MCB sector-advances page to rank one.
- LUCK expansion and utilisation: the Key lane surfaced exact utilisation
  evidence alongside management expansion commentary.
- Macro inflation and agriculture: generated Keys added direct food/energy
  and crop/livestock evidence that raw Markdown lanes ranked lower.
- MLCF acquisition follow-ups: generated announcement Keys improved direct
  acquisition-disclosure coverage.

Material Key regressions included:

- multi-bank sector exposure, where Key matches promoted generic ECL and
  foreign-currency exposure pages;
- broad LSM queries, where compact topic overlap did not reliably identify
  the answer-bearing industry-driver page;
- some announcement and bank-expense queries, where equal-weight Key fusion
  displaced an already-strong baseline result.

### Router revision

Holding the old representation fixed:

- nDCG@5: `-0.011`
- answer-bearing P@5: `+4.8pp`
- direct page @1: `-12pp`
- 23 wins / 5 ties / 22 losses

The router is improving breadth while worsening ordering. It often retrieves
more useful evidence somewhere in the first five, but the page shown first is
less dependable.

The largest structural router gain was MLCF/PIOC acquisition retrieval. The
old router selected only filings; the new router selected announcements plus
filings and surfaced the actual MLCF disclosures.

The largest structural router regressions were:

1. Filing cadence. The new router returned `any` where the old router and gold
   scope used `annual` in 25 cases. Quarterly pages then displaced annual note
   pages for deposits, capital ratios, and other bank comparisons.
2. Grouped entity resolution. “Big four banks” resolved to no tickers, so the
   search became a global capital-ratio query.
3. Comparison ordering. Multi-entity searches frequently put several pages
   from one company before the best page for each requested company.
4. Context-dependent queries. “These banks” and “the new capacity” were
   evaluated without prior-search context. Their absolute router scores are
   conservative and should become explicit follow-up cases in the next set.

Router scope checks reported 44/50 for the old router and 22/50 for the new
router. This does not establish that the old handcrafted router generalizes
better; most of its advantage comes from deterministic cadence/entity
assumptions embedded in this benchmark. It does establish that the new
model-only route contract is not yet preserving required scope reliably.

### End-to-end interaction

New router + new representation versus old router + old representation:

- nDCG@5: `+0.011`
- answer-bearing P@5: `+7.6pp`
- direct page @1: `-8pp`
- 20 wins / 6 ties / 24 losses

The new Keys rescue part of the router regression: under the new router they
move direct page @1 from 64% to 68%. They do not recover the old system's 76%
first-page rate.

## Intermediate Evidence

The accepted run contains:

- `run_manifest.json`: frozen commits, entity-directory gate, stage timing,
  token usage, and reused-judgment provenance;
- `routes/*.json`: raw old and new plans, HTTP status, and latency;
- `retrieval/*.json`: every field lane, query, candidate list, and final
  ranking for all four cells;
- `judgments/*.json`: blind scores and reasons against source Markdown;
- `cases/*.json`: four scored cells and query-level attribution;
- `eval_report.json` and `eval_report.md`: aggregate metrics and movements.

Two earlier runs are retained as rejected audit evidence:

- `2026-07-29T23-01-51.992353Z`: rejected because field queries were
  concatenated rather than executed as independent lanes;
- `2026-07-29T23-09-14.655126Z`: rejected because the entity-directory request
  returned 403 and industry filtering was silently unavailable.

The evaluator now fails closed when the entity directory is unavailable.

## Recommendation

1. Keep the generated Key as an additive lane.
2. Do not spend on more page enrichment yet.
3. Improve the router contract and prompt against typed scope outcomes:
   cadence, entity groups, source collection, temporal mode, and decomposed
   evidence needs. Do not reintroduce application-side phrase rules.
4. Make multi-entity first-page coverage an explicit ranking objective.
5. Replace model-only judgments with human qrels for the material wins,
   regressions, and a larger untouched holdout.
6. Rerun the same factorial. Expand enrichment only if the Key lane retains
   its lift after router correction and human review.
