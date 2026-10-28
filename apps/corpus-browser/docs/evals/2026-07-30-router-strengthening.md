# Router Strengthening — 2026-07-30

## Decision

Keep `gemini-3.5-flash-lite` at `medium` reasoning as the sole natural-language
retrieval controller.

The router is materially more reliable after making answer-bearing page
selection its primary objective and simplifying the model contract to one
canonical facet-based execution shape. Do not add a deterministic router,
query-specific synonym map, regex rescue, automatic corpus fallback, or
another model reviewer.

The remaining visible failures are predominantly page-ranking and abstention
problems rather than routing problems.

## Structural changes

- Every requested evidence need now has exactly one search facet.
- Root-level lexical and field queries were removed from the model response;
  the primary facet supplies the UI-level query fields.
- Every facet chooses exactly one authoritative corpus. Cross-corpus research
  requires separate evidence needs and facets.
- The controller plans in this order: requested outputs, corpus, entity and
  time scope, ideal answer-bearing page, then field-specific queries.
- Field queries explicitly target the direct note, table, primary statement,
  event notice, chapter, or narrative discussion rather than a neighboring
  parent section.
- Required entities define source scope. Supporting transaction entities stay
  in the search language but no longer widen retrieval to their own unrelated
  documents.
- Route-level corpora and cadence must agree with the facet plan.
- Catalog industries and unresolved named-company groups cannot coexist.
- Follow-up searches inherit prior entities through the typed application
  boundary; the model only returns entities explicitly named in the new turn.
- A one-word heading is valid. The old two-word minimum rejected useful direct
  headings without improving retrieval.

## Evaluation

### Final medium-reasoning run

The final run was split by a temporary network interruption. The first 19
cases completed before connectivity failed; the interrupted 25-case slice was
rerun after connectivity was restored.

- First slice:
  `artifacts/corpus_browser_router_variety/runs/2026-07-30T14-23-06-167Z/eval_report.json`
  (`results[0:19]` only)
- Rerun slice:
  `artifacts/corpus_browser_router_variety/runs/2026-07-30T14-27-28-780Z/eval_report.json`

Combined result:

| Measure | Result |
| --- | ---: |
| Route and execution contracts | 44 / 44 |
| Retrieval HTTP failures | 0 |
| Median router + retrieval latency | 5,034 ms |
| P90 router + retrieval latency | 9,089 ms |
| Maximum router + retrieval latency | 12,300 ms |

The interrupted report's subsequent `fetch failed` rows are excluded. One
elapsed time jumped by roughly 2.6 hours while the machine or network was
paused, so that run is not a valid latency or quality measurement.

The retained pre-change 26-query run was also 26/26, with 5,330 ms median and
10,623 ms P90 latency. The important improvement is not the pass count; it is
the stronger execution contract and the harder paraphrase coverage.

### Reasoning-effort comparison

Fifteen difficult cases were run at both reasoning levels:

| Setting | Contracts | Median | P90 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Medium | 15 / 15 | 5,882 ms | 10,445 ms | 12,383 ms |
| Low | 13 / 15 | 3,664 ms | 6,699 ms | 6,809 ms |

Low reasoning was materially faster but failed an implicit announcement event
and an unresolved named-company group. Medium remains the safer production
choice.

## Manual first-page audit

### Clearly improved

- `HBL latest cash balance`, `What are HBL's most recent balances with treasury
  banks?`, and `How is HBL's latest cash balance composed?` all surfaced the
  2026-03-31 `CASH AND BALANCES WITH TREASURY BANKS` note first.
- The explicit annual variant returned the same direct note for 2025.
- Both UBL insider-transaction phrasings returned the correct director,
  executive, spouse, and substantial-shareholder notices.
- The MLCF acquisition-notice variant searched MLCF sources only; PIOC remained
  a discriminating counterparty instead of admitting unrelated PIOC notices.
- Named and inferred macro variants consistently selected Economic Survey or
  SBP material and the expected Agriculture, Inflation, Money and Credit, and
  Fiscal Development chapters.
- The explicit HBL modelling request surfaced the income statement, statement
  of financial position, and cash-flow statement as the first three facets.
- Cement capacity, marketing expense, board attendance, deposits, and capital
  adequacy variants generally surfaced direct tables or note pages.
- `big four banks` now fails closed because group membership is not present in
  the entity catalog.

### Still not excellent

- The less explicit UBL financial-model query can still rank a financing-
  liabilities reconciliation ahead of the primary cash-flow statement and a
  USD note ahead of the main balance sheet. The router asked for the correct
  statements; candidate ranking selected the wrong pages.
- ABL dividend-policy retrieval can rank a green-banking policy page because
  the current OR-token field retrieval overvalues the generic word `policy`.
- Some peer sector-exposure results cover every bank but promote a
  public/private or portfolio-classification page instead of the direct
  industry-sector table for one company.
- A request for the latest annual impact of an acquisition can legitimately
  refer to a filing that predates the acquisition. Retrieval currently fills
  that facet with weak pages instead of reporting that the requested evidence
  is not yet available.
- ECL methodology queries can rank quantitative Stage 1/2/3 tables and key
  audit matters ahead of the direct assumptions and methodology page.
- Router plus retrieval P90 remains above nine seconds.

## Conclusion

The stronger router substantially improves corpus choice, query formulation,
entity scope, comparison decomposition, and direct-page targeting. It also
makes route failures easier to classify because there is now only one
controller execution shape.

It does not make first-page ranking world-class by itself. The next structural
work should use human page judgments to calibrate directness, exact field
agreement, abstention, and section-level ranking. More router prompt rules are
not the next move unless a judged failure is demonstrably a route error.
