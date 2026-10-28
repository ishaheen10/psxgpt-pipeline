# Filing structure-index ablation

## Question

Does a real index over existing Markdown headings and table headers improve
page retrieval without re-parsing PDFs, generating more page tags, or adding
query-specific rules?

## Implementation

The existing filing retrieval path called one `search_text` query a heading
lane. It was a useful router-generated query morph, but not an independent
heading representation.

The v4 experiment keeps every existing lane and adds two thin, mechanically
derived FTS5 fields:

- Markdown headings, including explicit Markdown and standalone bold headings;
- table headers identified from Markdown table syntax.

Body prose is not copied into either field. Missing structure remains empty.
Search against these short fields requires all meaningful field-query terms,
so the new lane behaves as a precision signal rather than a broad recall lane.

Every sidecar row is joined to D1's canonical `jpg_path`, ticker, filing period,
cadence, and page number. Folder dates are not inferred.

## Coverage

| Measure | Pages |
| --- | ---: |
| Live filing pages in D1 | 256,607 |
| Structure-index rows mapped to live D1 pages | 256,607 |
| Pages with at least one heading | 199,846 (77.9%) |
| Pages with at least one table header | 122,202 (47.6%) |
| Extra local Markdown pages not in live D1, intentionally excluded | 20,119 |

Announcements already have an independent title index. Macro pages already
have an independent chapter index. This experiment therefore adds structure
only where the live retrieval contract was missing it: filing pages.

## Evaluation design

The evaluator reuses the exact 20 routed plans from the accepted
`user-fact-search-first-turn-v1` run. It does not call the router again.
For every plan it retrieves and hydrates the visible UI groups twice:

1. existing retrieval (`baseline`);
2. existing retrieval plus the new structure lanes (`structure`).

The final production-shaped artifact is:

`artifacts/corpus_browser_structure_ablation/runs/2026-07-30T19-34-08-205Z/eval_report.json`

## Results

| Measure | Baseline | With structure | Change |
| --- | ---: | ---: | ---: |
| Visible result sets changed | — | 6 / 20 | bounded |
| Median retrieval latency | 824 ms | 1,128 ms | +304 ms |
| p90 retrieval latency | 1,612 ms | 1,943 ms | +331 ms |
| Mean retrieval latency | 901 ms | 1,165 ms | +264 ms |

Manual Markdown review of all six changed result sets:

- **BIPL current-account growth:** material improvement. Customer deposit-note
  pages replace pages about the bank's own balances in current accounts.
- **FABL individual deposits:** improvement. The irrelevant deposit-ratio card
  is removed and the visible set becomes deposit-note pages.
- **NRL segment breakdown:** improvement. A newer page describing the segment
  reporting change becomes the group leader.
- **Bank exposure and capital ratios:** small improvement. UBL's third capital
  card becomes another direct CET1-capital page; all eight company/facet groups
  remain intact.
- **MEBL liability breakdown:** mixed but relevant. The newest `Other
  liabilities` note replaces a high-level vertical-analysis page.
- **Interloop balance sheet:** neutral. The third card changes from horizontal
  to vertical statement-of-financial-position analysis; the two leading cards
  are unchanged and neither version yet finds the primary statement.

No changed result set was judged clearly worse. Fourteen of twenty result sets,
including HBL latest cash, UBL sector exposure, announcements, macro browsing,
and cement expansion, were unchanged.

## Decision

Ship the structure index as an additive filing signal in
`ledger-retrieval.v4`.

Do not treat the result as proof that headings solve ranking generally. The
gain is concentrated where the user asks for a disclosure with a stable source
heading. The Interloop failure confirms that primary-statement ranking remains
a separate unresolved problem.

The UI no longer presents an automatically assembled "preferred" context
across dates. It exposes the ranked company/facet groups directly and requires
the analyst to add pages explicitly.
