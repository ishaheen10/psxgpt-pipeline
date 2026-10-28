# Ledger context-quality evaluation — 2026-07-28

## Verdict

The deployed router and fielded BM25 ranker are materially better than raw
natural-language BM25, but the first few visible pages and automatic research
bundle are **not yet consistently excellent**.

Exact, recurring disclosures perform very well. Open-ended narratives, corpus
selection for implicit macro queries, entity disambiguation, and result
diversity still create visibly poor first-page experiences.

## Runs

### Production-shaped 50-query benchmark

| Mode | Pass rate | Mean Precision@5 | Median latency | p90 latency |
|---|---:|---:|---:|---:|
| Raw natural-language BM25 | 33/50 (66%) | 54.6% | 1,892 ms | 2,663 ms |
| Gold-routed Q/K field search | 50/50 (100%) | 97.6% | 445 ms | 1,623 ms |
| Deployed Gemini router + ranker | 49/50 (98%) | 87.2% | 2,172 ms | 3,336 ms |

The end-to-end benchmark's relevance rule is deliberately broad: it counts a
page when its visible key contains the expected topic terms. It is useful for
regression detection, but it does not prove that the page contains usable
evidence or that the first pages cover the requested peers.

### Strict answer-readiness audit

Fourteen representative query families were routed live. The first three pages
and the exact bundle produced by the UI's selection policy were hydrated to
full Markdown and checked for:

- exact entity and corpus scope;
- a query-specific title, summary, heading, or table key;
- substantive Markdown rather than a cover, contents, or sparse page;
- usable numbers or tables for quantitative questions;
- peer diversity in the first three pages;
- facet × ticker and requested-period coverage in the bundle.

Two repeated strict runs produced:

| Measure | Run 1 | Run 2 |
|---|---:|---:|
| Exact router scope | 10/14 | 10/14 |
| Excellent first three | 2/14 | 2/14 |
| Answer-bearing pages in first three | 28/42 | 27/42 |
| Excellent automatic bundles | 7/14 | 8/14 |
| Answer-bearing pages in bundles | 37/52 | 39/53 |

The repeated-run range matters because the Gemini router can produce slightly
different facet wording and field queries.

## What was genuinely excellent

- **Bank sector exposure:** the leading pages were exact “Credit risk by
  industry sector” tables with hundreds of numeric cells. The bundle selected
  one strong page for ABL, HBL, MCB, and UBL.
- **Bank capital ratios:** exact CET1/capital adequacy pages were returned and
  the bundle covered all four banks.
- **Deposits and funding:** exact “Deposits and other accounts” notes were
  answer-bearing and the bundle covered HBL and UBL.
- **Marketing expense:** exact “Marketing, advertisement and publicity” pages
  were found for FABL, MEBL, and UBL.
- **Cement capacity:** the first three pages were exact capacity tables from
  three different companies.
- **NRL product revenue:** the first three were mixed, but requested-period
  diversity recovered one usable page for each of 2021–2025 in the bundle.
- **Pharma currency risk:** exact currency-risk notes were found for AGP and
  Highnoon.
- **Macro inflation:** the top pages came from the Economic Survey inflation
  chapter and contained usable quantitative evidence.

## Material failures

1. **Peer comparisons are ordered by entity blocks.** For sector exposure,
   capital ratios, deposits, marketing spend, and pharma FX risk, all first
   three pages came from the first company. The pages were individually strong,
   but the first screen was not a comparison. The bundle's facet × entity
   policy repaired this later.

2. **Implicit macro queries leak into filings.** “Weak major crops versus
   livestock growth” and “Which industries drove the LSM contraction?” routed
   to both filings and macro. Company annual-report commentary occupied the
   first pages; the Economic Survey appeared only later.

3. **Entity resolution over-matches company names.** “Tariq Glass” resolved to
   both TCORP and TGL. The first page was a valid TCORP fuel-and-power table,
   which is excellent text for the wrong company.

4. **Open-ended company narrative retrieval is weak.** The LUCK expansion and
   utilisation query returned financial-information, organizational-overview,
   and value-added pages instead of management discussion of capacity and
   expansion.

5. **Announcements admit incidental matches.** The MLCF acquisition results
   included a director-interest disclosure, and the SYS contracts query
   included quarterly financial results as the third page.

## Priority fixes

1. Interleave the visible results one page per requested entity before showing
   a second period for the same entity.
2. Make macro intent exclusive when the query asks for national sector,
   inflation, crop, livestock, LSM, GDP, or policy evidence without naming a
   company.
3. Resolve the longest exact company-name match first and suppress overlapping
   aliases such as Tariq Corporation when Tariq Glass is explicit.
4. Add answer-bearing score features already available at query time:
   query-key match in heading/summary, table/numeric density, and penalties for
   generic or mismatched headings.
5. For announcements, require the event key in the announcement title,
   `search_text`, or opening paragraph before admitting a result to the first
   page.
6. Treat the strict context audit as the release gate; keep the 50-query
   benchmark as the broader regression suite.
