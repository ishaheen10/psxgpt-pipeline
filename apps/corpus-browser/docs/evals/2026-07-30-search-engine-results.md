# Search-engine evaluation: recent-first and company-grouped results

## Product contract

Ledger is being evaluated as a document search engine, not as an analyst.

Its job is to:

1. route a natural-language query to the right company, collection, period, cadence, and independent search facets;
2. retrieve directly relevant source pages;
3. show newer relevant pages before older equivalents;
4. group multi-company searches by company, with the requested facets inside each company;
5. let the user read, select, and compile those pages.

The browser is not judged on calculations, derived metrics, or answer synthesis.

## Change under test

The search path now has:

- one model-owned route from `gemini-3.5-flash-lite` with medium reasoning;
- provider-enforced structured output;
- no semantic validator, corrective planning retry, regex resolver, or query-specific fallback;
- a default recency preference for ordinary browsing;
- a distinct hard newest-snapshot filter for `latest` queries;
- newest-first cards inside each visible result group;
- company-first result sections when more than one ticker is requested;
- evidence-facet groups nested inside each company section.

The evaluator records and hydrates the same company and facet groups presented by the UI. This avoids judging a misleading global top-three list for multi-company searches.

## Evaluation set

[user-fact-search-first-turn-v1.json](../../evals/user-fact-search-first-turn-v1.json) contains 20 anonymized fresh searches taken from `psxgpt-serve` production history. Calculation-oriented cases were removed. The set now covers:

- announcements and insider transactions;
- primary statements and financial notes;
- operating and segment disclosures;
- recent and multi-period source browsing;
- macro-style industry discovery;
- multi-company, multi-facet search.

The production-path run is:

`artifacts/corpus_browser_user_fact_search_first_turn_v1/runs/2026-07-30T17-30-01-088Z/eval_report.json`

Manual Markdown judgments are in [user-fact-search-first-turn-v1-judgments.json](../../evals/user-fact-search-first-turn-v1-judgments.json).

## Results

| Search-engine measure | Result |
| --- | ---: |
| Executable route contract | 20 / 20 |
| Zero-result queries | 0 / 20 |
| Directly relevant group leader | 23 / 31 |
| Directly relevant visible cards | 63 / 87 |
| Every visible group starts relevant | 12 / 20 queries |
| Complete visible coverage of the user need | 6 / 20 queries |
| Median router latency | 3,651 ms |
| p90 router latency | 5,458 ms |
| Median retrieval latency | 965 ms |
| p90 retrieval latency | 1,425 ms |
| Median route + retrieval + hydration | 4,940 ms |
| p90 route + retrieval + hydration | 7,086 ms |

The route-contract result is now strong. It must not be confused with search quality: only 23 of 31 visible company/facet groups put a directly relevant page first.

## What is working

### Routing and source scope

All 20 fresh queries produced executable routes with the required named tickers and collections. There were no validator-induced failures and no zero-result searches.

### Recent lookup

The newest quarterly cash note ranks first for HBL. Recent UBL announcements and ENGROH interest disclosures are ordered by date. UBL sector exposure starts with the 2025 disclosure.

### Multi-company browsing

The bank comparison is now presented as four company sections:

```text
UBL
  Sector level exposure
  Key capital ratios
HBL
  Sector level exposure
  Key capital ratios
ABL
  Sector level exposure
  Key capital ratios
MCB
  Sector level exposure
  Key capital ratios
```

This exposes all eight company-by-facet cells without asking the user to mentally untangle one interleaved ranking.

### Stable disclosure headings

Search is strongest where the requested concept has a stable source heading:

- cash and balances with treasury banks;
- credit risk by industry sector;
- deposits and other accounts;
- capital adequacy ratios;
- reportable segments;
- exchange announcement titles.

## What is not working

### Relevance still loses to adjacent concepts

The most important failures are relation failures, not route failures:

- BIPL customer current-account deposits lose to pages about the bank's own balances held in current accounts.
- FABL individual-deposit composition ranks below current-deposit and deposit-ratio pages.
- NRL base-oil and other-segment evidence ranks below fuel-segment narrative.
- ENGROH's subsidiary financial-summary page ranks below generic cash-flow statements.
- ABL's capital-ratio group contains glossary and risk-overview pages instead of the ratio table.

The Key and ranking layers need to distinguish the requested relationship, not merely the requested nouns.

### Freshness cannot rescue a weak candidate set

The UI can order retrieved pages newest-first, but it cannot surface a newer exact page that candidate retrieval did not find. Recency should remain a ranking signal applied after topical relevance, not a substitute for it.

### Repeated periods crowd out requested coverage

UBL's six-quarter deposit search starts with two pages from the same quarter. CNERGY and NRL return useful segment pages but do not expose the requested five-year range on the first screen.

This is a search-results contract issue: period diversity is a browsing dimension, just as company and facet are. It does not require a calculation engine.

### Some searches have no exact disclosure

GVGL auto-glass market share and OTSU syringe manufacturing did not produce a directly matching page. The browser should not pretend that production utilization or generic plant capacity is the requested evidence.

This should be represented as weak search relevance or absent evidence, not converted into an analyst task.

### One facet can hide two distinct user needs

For “recent announcements, statements and outlook for UBL,” the router combined statements and outlook into one facet. The pages are relevant to outlook, but no primary statement page appears. Independent retrieval needs should remain independently visible when the user explicitly names them.

## Structural next work

No validator, regex fallback, or query-specific exception is warranted.

The next search improvements should target:

1. relationship-sensitive ranking across headings, Keys, summaries, and page text;
2. distinct-period coverage inside bounded or multi-period result groups;
3. duplicate suppression before cards are shown;
4. independent facets for independently requested source types;
5. calibrated weak-match handling when no directly relevant disclosure is retrieved.

The release gate should remain search-specific: direct relevance at rank one, precision of visible cards, company/facet/period coverage, zero-result rate, and latency.
