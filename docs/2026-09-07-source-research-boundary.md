# Source research publication boundary — 7 September 2026

The serving product now uses original Markdown, section tags, raw PSX snapshots and a
small independent aggregate index. It bypasses full statement extraction, ratios and LTM.
The authoritative product contract is `psxgpt-serve/apps/analysis/spec.md`; existing pipeline
chains remain available for other consumers and have not been disabled.

Two independent utilities in the serving repo accept document-publication page rows:
`apps/analysis/scripts/tag-research-pages.mjs` and `build-research-index.mjs`. They produce
reviewable SQL/reports; the latter reads original page PDFs and copies only reported
deposits, assets and equity. It does not consume `financial_statements` or derive periods.

The tagger optionally takes this repo's `review/` root as its third argument to synchronize
review JSONL before a later Step 17 publish. On 2026-09-07, 76 March 2026 bank-report pages
were retagged in D1 and synchronized to their existing review artifacts. This is a scoped
repair, not a corpus-wide retagging claim. The source Markdown and search text were preserved.

The correction separates equity-change statements and Islamic-business notes from primary
balance sheets, keeps contents pages untagged, and leaves unprinted basis unknown. The
Step 04 instructions now explicitly preserve these distinctions for future tagging.

Evidence and publication SQL are in the serving repo's ignored
`eval/outputs/simplification-2026-09-07/`. Aggregate coverage is 15 bank deposit observations,
12 assets and 12 equity observations at 2026-03-31, unconsolidated; excluded observations
are not guessed or reconstructed. Existing financial statement/ratio tables are unchanged.
