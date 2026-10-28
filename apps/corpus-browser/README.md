# Ledger corpus browser

Ledger is a human-in-the-loop retrieval UI for the authoritative corpus produced
by `psxgpt-pipeline`.

It deliberately avoids structured financial-data search. A narrow search
router translates ordinary language into ticker, year, corpus, and three short
search keys for the indexed summary, page text, and likely heading. A small
read-only Cloudflare Worker runs exact-phrase and strict FTS5 BM25 searches
over those fields independently, broadens only when the strong lanes are
sparse, then combines their ranks with weighted reciprocal-rank fusion. The
Worker derives real headings and table signatures from existing Markdown and
uses those signals for deterministic reranking; unreliable general
`section_tags` are not used for filing titles or ranking. The router never
retrieves documents or answers the question. Its
inferred scope and field queries are visible after search and can be adjusted
when needed. Ticker/year searches are balanced across every requested scope
instead of letting one company dominate the global ranking. Results show the
indexed `search_text` summary separately from the matched body excerpt.

A selected page can become a new query prototype. Ledger extracts its specific
heading and table labels, combines them with the active research facet, and
searches for comparable disclosures across the current companies or periods.
The original Markdown remains the value read by the analyst; only its compact,
content-derived key is searched. The source reader also expands into adjacent
pages so split tables and continuation notes stay available without indexing
new metadata.

The router receives the prior search scope for likely conversational follow-ups.
This matters because the production query-log audit found that 1,581 of 4,134
queries occurred after another query in the same session and 1,671 matched more
than one retrieval intent. Distinct evidence needs are therefore routed as
separate facets rather than collapsed into one broad BM25 query.

Deterministic financial-statement packs are currently disabled. Financial
statements, notes, schedules, filing narratives, announcements, and macro
sources all use the same field-lane architecture so retrieval quality can be
evaluated without a shortcut or corpus rebuild.

The UI lets a researcher choose which pages belong in Analyst Context, choose
summary or bounded full-page Markdown per source, inspect scoped deterministic
quality flags, and compile an answer from only the selected evidence. Results
are grouped by routed evidence need first and company second. A coverage matrix
shows found, selected, and missing facet × entity cells. **Start here** presents
the best available page for each evidence need and requested entity; **Add best
pages to context** fills the missing cells before adding useful period
diversity. This is what company interleaving means in practice: a four-company
query leads with useful coverage across all four companies instead of filling
the first screen with several pages from whichever company happens to score
highest globally. Individual pages and the complete evidence bundle can be
copied as Markdown.

On mobile, selecting evidence and compiling it are persistent bottom actions.
The context drawer uses a full-height phone layout, and the page reader,
Markdown copy action, coverage state, and compilation result remain available
without returning to a desktop-only workspace.

Ledger now exposes the corpus through a small catalog contract rather than
turning every extracted label into a filter. The stable hierarchy is
`corpus → document → page`; publisher, document type, entity, canonical
industry, period, and filing cadence are shared dimensions. Search still
operates over summary, page text, and heading keys, while the original Markdown
is the value opened or sent to the analyst model.

Facets are contextual: Ledger only shows entity, industry, period, source type,
or publisher choices that materially split the current result set. Choosing a
facet reruns the current research query with that scope instead of creating a
second browsing mode. Pipeline industry strings are normalized into a stable
two-level taxonomy at the catalog boundary, without retagging pages or changing
their source records.

Selected pages form a persistent, user-owned research cart in D1. Each line stores the
exact corpus and page ID, its document and catalog IDs, provenance, retrieval
facet, selection method, and inclusion mode. The full Markdown remains in the
source corpus and R2 boundary; it is rehydrated when the session is opened and
is only included at compilation time. Compilation therefore behaves like a
checkout of exact evidence rather than a fresh retrieval request.

Every completed search is also appended to the authenticated research session
with its routed scope and ranked result snapshot. The visible research trail
lets an analyst reopen an earlier result set while keeping the same accumulated
context. Exploratory searches do not replace the session's analyst objective.

The browser uses the same Supabase project as `psxgpt-serve`. Google OAuth and
email magic-link login are supported. UI API routes verify the Supabase access
token, and the API Worker binds each research session to the authenticated user
before returning or changing its cart.

Each result can also be labeled `ideal`, `relevant`, `supporting`,
`irrelevant`, `wrong_scope`, or `navigation_noise`. These judgments are stored
as lightweight evaluation observations. They do not change live ranking
silently; they create the judged-query set needed to measure and improve it.

## Storage boundary

- D1 `financial_documents_new` + `filings_fts_new`
- D1 `psx_announcements` + `psx_announcements_fts`
- D1 `macro_documents` + `macro_fts`
- D1 sidecar `page_structures` + `page_structures_fts` for filing Markdown
  headings and table headers, keyed back to canonical live filing paths
- D1 `data_quality_flags` for visible deterministic checks
- D1 `corpus_relevance_feedback` for explicit analyst judgments
- D1 `catalog_industries` + `catalog_industry_aliases` for stable taxonomy
- D1 `research_sessions` + `research_cart_lines` for persistent evidence carts
- D1 `research_searches` for the persistent query and result trail within a project
- D1 logical views `catalog_documents`, `catalog_pages`, and
  `catalog_search_keys` over the existing corpus tables
- R2 bucket `psx` for filing, announcement, and macro source assets

The sidecar contains only compact, query-independent structure derived from
existing Markdown; it does not copy page prose. The API Worker is read-only for
corpus and sidecar content. Its bounded write paths store analyst judgments and
research-cart state; document and asset endpoints only issue `SELECT`
statements and R2 `head` requests.

## Local development

```bash
npm install
npm run dev
```

The UI proxies `/api/catalog`, `/api/stats`, `/api/tickers`, `/api/search`,
`/api/similar`, `/api/document`, `/api/cart`, and `/api/feedback` to
`CORPUS_API_URL`. The default is the deployed read-only corpus API. Override it
in a local `.env` when testing a different API deployment. `/api/route` is a
private server route using `GEMINI_API_KEY` and
`gemini-3.5-flash-lite`; it validates all resolved entities against the
authoritative ticker directory and fails explicitly when it cannot produce a
valid plan. It does not synthesize a literal fallback. Inferred and explicit
source scopes both select the smallest sufficient corpus set; an explicit
scope additionally has to quote the user's source phrase. `/api/compile` is a
private server route and requires
`DEEPSEEK_API_KEY`; the model name defaults to `deepseek-v4-pro` and can be
overridden with `DEEPSEEK_ANALYST_MODEL`.

Both the UI Worker and API Worker require `SUPABASE_URL` and
`SUPABASE_ANON_KEY`. These are deployed as Cloudflare secrets. The anon key is
safe to expose to the browser, while authorization still depends on a valid
user access token and server-side ownership checks.

Compilation is bounded to 16 sources, 12,000 characters per source, and 60,000
characters overall. Source content is explicitly treated as untrusted evidence,
and the model must cite source numbers and report missing coverage.

## Validation and deployment

```bash
npm test
npx wrangler d1 migrations apply psx --remote --config wrangler.api.toml
npm run eval:retrieval -- https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev
npm run eval:retrieval:50 -- https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev
npm run eval:retrieval:50 -- https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev \
  --router-base=https://your-ledger-site.example
npm run eval:router-variety
npm run eval:key-ablation
npm run eval:structure-ablation
npx wrangler deploy --dry-run --config wrangler.api.toml
npx wrangler deploy --config wrangler.api.toml
```

The 50-query evaluation is modeled on production D1 query shapes. It compares
the natural-language query sent directly to BM25 with a gold-scoped routed
query using separate summary, text, and heading keys. This isolates ranker
quality from model-router quality. Passing `--router-base` adds a true
end-to-end run through the deployed router and the live ranker. The report
includes pass rate, route-scope accuracy, Precision@5, generic-title rate,
latency, per-family results, and individual failures.

`eval:router-variety` runs diverse controller shapes through the production
router materializer, retrieval executor, and facet merge path. Every run is
retained under `artifacts/corpus_browser_router_variety/runs/`; focused replays
therefore do not overwrite the latest comprehensive report.

`eval:key-ablation` is the production-shaped generated-Key experiment. It uses
the same router materialization, retrieval executor, and context-bundle
selection code as the browser. Baseline, Key-only, and combined modes execute
against the isolated Worker in `wrangler.api.key-eval.toml`; production search
is not changed. Route and page judgments are separate and content-addressed,
and every run is retained under
`artifacts/corpus_browser_key_ablation/runs/`. The 2026-07-29 result and its
coverage limitations are recorded in
`docs/evals/2026-07-29-key-ablation.md`.

`eval:structure-ablation` reuses a single set of saved router plans and compares
the same visible result groups with and without the filing structure sidecar.
The accepted v4 run and manual Markdown review are recorded in
`docs/evals/2026-07-30-structure-index-ablation.md`.

The UI and corpus API are packaged as separate Cloudflare Workers. Keeping them
separate lets the existing production D1 and R2 remain the single source of
truth while the browser retains private server routes for routing and answer
compilation.

Production browser:
<https://psxgpt-corpus-browser.isfandiyar-shaheen.workers.dev>
