# Router and Ranker Refinement — 2026-07-30

## Decision

The deployed browser is materially better, but the visible first-page audit
does not yet justify calling it a world-class page ranker.

This change should ship because it fixes execution-boundary errors rather than
adding query-specific rescue rules:

- the controller now emits typed entity roles, evidence needs, per-facet
  corpus/cadence/temporal scope, and four independent retrieval keys;
- every resolved company must be proven by the supplied ticker directory;
- exact ticker identity takes precedence over ticker text appearing in another
  company's legal name;
- `latest` means one newest snapshot while `recent` means a recency-weighted
  set;
- each facet is retrieved and reranked against its own evidence need;
- required-company coverage is promoted only from sufficiently supported
  candidates;
- supporting transaction entities cannot substitute for the requested company;
- baseline and generated-Key lanes no longer receive equal authority when the
  optional Key index is enabled;
- card titles are selected from query-matched headings, page signals, and
  table signatures already present in the Markdown.

No statement pack, regex router, query-specific synonym map, deterministic
search fallback, or eval-specific corpus rescue was added.

## Production Boundary

- Browser:
  <https://psxgpt-corpus-browser.isfandiyar-shaheen.workers.dev>
- Search API:
  <https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev>
- Search API version deployed after this run:
  `ledger-retrieval.v3`
- Router model:
  `gemini-3.5-flash-lite`, medium reasoning
- Production retrieval mode:
  existing summary/text/heading baseline; the partial generated-Key sidecar
  remains disabled in production.

## Retained Evaluation

The router-variety evaluator now uses the same `materializeModelRoute`,
`executeRetrievalPlan`, and facet merge path as the browser. Every run is
written to an immutable timestamped directory rather than overwriting the last
report.

Run:

`artifacts/corpus_browser_router_variety/runs/2026-07-30T10-19-05-928Z/eval_report.json`

Results:

| Measure | Result |
| --- | ---: |
| Route and execution contracts | 26 / 26 |
| Retrieval HTTP failures | 0 |
| Median router + retrieval latency | 5,330 ms |
| P90 router + retrieval latency | 10,623 ms |
| Maximum router + retrieval latency | 11,605 ms |

The 50-query gold-scoped ranker suite also passed 50/50 with mean
Precision@5 of 0.941, compared with 32/50 and 0.524 for raw natural-language
BM25. Median gold-scoped retrieval latency was 508 ms and P90 was 1,133 ms.
Those labels are lexical-family checks, not proof that every first page is
answer-ready; the manual audit below remains authoritative for product quality.

## Manual First-Page Audit

Clear improvements:

- `UBL insider purchases and sales` routes only to announcements and returns
  direct insider-transaction notices.
- `HBL latest cash balance` selects the 2026-03-31 quarterly filing instead of
  2024 pages. The top page contains the exact table and now displays
  `5 CASH AND BALANCES WITH TREASURY BANKS`.
- annual versus quarterly cadence is independently scoped rather than inferred
  from recency.
- multi-company sector exposure and capital-ratio research is split into two
  evidence needs and assembled as an entity-by-need bundle.
- a Maple Leaf / PIOC acquisition query treats MLCF as required and PIOC as
  supporting, with the direct MLCF material-information notice first.
- macro agriculture, inflation, policy-rate, and named Economic Survey chapter
  requests remain in the macro collection and return the expected chapters.
- exact note, segment, governance, ECL, marketing-expense, and refinery-margin
  shapes generally surface direct pages in the first positions.
- misspelled companies and undefined groups such as `big four banks` fail
  closed instead of silently selecting a plausible but ungrounded ticker set.

Remaining mixed results:

- peer coverage can still promote a lexically strong neighboring disclosure
  for one company when that company's direct table has weaker FTS support;
- some capacity tables share a page with an unrelated note. Table-signature
  titles now expose the requested table, but page-section boundaries are still
  imperfect;
- industry-wide requests do not yet have a judged diversity objective, so the
  first page set can overrepresent one publisher;
- transaction requests that ask for both an event and later financial-report
  impact can retrieve a strong announcement but a weak filing page;
- a correct route can legitimately return no pages when the requested bounded
  period is absent, as with UBL dividend announcements in 2025;
- router plus retrieval P90 above ten seconds is too slow for an excellent
  interactive browser.

## Next Structural Gains

1. Build judged first-page and compiled-context labels for directness,
   answer-readiness, entity correctness, and evidence-need coverage. Do not
   optimize against keyword presence alone.
2. Calibrate an abstention threshold for required-company slots so a missing
   direct page produces an explicit coverage gap instead of a neighboring
   disclosure.
3. Derive section and table boundaries from existing Markdown so a table can
   be titled and scored independently of an adjacent note. This does not
   require LLM retagging.
4. Add an authoritative alias/group catalog only for group phrases the product
   intends to support. Do not let the router invent group membership from model
   memory.
5. Reduce router latency by measuring single-pass medium reasoning against a
   compacted prompt before adding another model stage.

More page tagging is not the next step. The highest-value remaining work is
evaluation quality, section-level evidence scoring, calibrated abstention, and
router latency.
