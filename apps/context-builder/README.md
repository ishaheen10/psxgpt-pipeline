# Ledger context builder

A human-in-the-loop retrieval UI over the `psxgpt-pipeline` corpus.

The premise is that `psxgpt-serve` fails by retrieving the wrong context
automatically. This app removes the automation from that step. It runs BM25 over
the source text, shows the researcher what matched and how well, and lets them
decide page by page what belongs in the analyst context. Only the selected pages
are sent to a model.

The loop is **search → inspect → assemble → send**.

## What is deliberately absent

- No embeddings and no vector store. Ranking is SQLite FTS5 `bm25()`.
- No model in the retrieval path. A typed query is reduced to BM25 terms and
  explicit scope by fixed rules in `lib/query.ts`, and the interpretation is
  shown back to the researcher.
- No second copy of the corpus. Search goes to the deployed read-only Worker,
  which issues only `SELECT` and R2 `head`.

## Storage boundary

This app owns no data. It proxies to the corpus API in
`apps/corpus-browser/api/worker.ts`, which reads:

- D1 `financial_documents_new` + `filings_fts_new` — 256,607 filing pages
- D1 `psx_announcements` + `psx_announcements_fts` — 8,847 notices
- D1 `macro_documents` + `macro_fts` — 1,041 macro pages
- D1 `data_quality_flags` — the deterministic checks shown on results
- R2 `psx` — page PDFs and thumbnails, checked live per selected source

## Search guidance

BM25 over this corpus rewards terms describing what a page *discusses* and punishes terms
describing which *section* it belongs to. A contents page is short and repeats every
statement name, so `financial statements` ranks it above every real page — measured at
**52% of the top 5 being navigation pages** for ticker-scoped structural queries.

The serving engine solves this by giving its lane model a vocabulary inventory
(`psxgpt-serve/apps/analysis/context/lane/fact/financial_documents_new.md`). Here the human
writes the query, so the same knowledge is in the UI instead of in a prompt:

- **Search terms** panel — high-signal terms grouped by topic and sector, click to add.
- **Query advice** — naming a low-signal phrase reports what it will actually return
  (`financial statements → contents pages, which list every statement by name`) and offers
  replacements. It advises rather than rewrites; the query still runs as typed.

`lib/query.ts` no longer substitutes `"financial statements"` when scope-stripping empties a
query. That fallback made the worst possible query the default.

## Recurring disclosures

Annual reports restate boilerplate almost verbatim, and BM25 has no notion of a document
family. On a real query (`UBL MCB cost of deposits and interest rate sensitivity`), four of
six MCB result slots were the same share-price-sensitivity table from 2021 through 2024,
pushing the page that actually discussed deposit cost down to rank five.

Near-identical summaries within one company now collapse into a single row offering the other
years:

```
restated in 4 filings — showing  [2024] 2023  2022  2021
```

Which year belongs in the pack is the researcher's decision, so nothing is discarded — the
repeats move behind a picker. Similarity uses the overlap coefficient rather than Jaccard,
because a restatement routinely swaps details (MCB's 2024 page replaces "political stability
law order" with a market capitalisation) and Jaccard charges for each difference twice —
scoring that obvious restatement 0.62 while an unrelated page scores 0.07. Overlap scores the
same pair 0.81. Numbers are excluded from the comparison entirely, since figures are exactly
what should differ between years.

## Reading and clipping

A search result is a 500-character snippet. Judging a page from it means judging a page you
have not read, and a schedule routinely continues onto the next page. Both are the same
problem: the decision needs the document, not a fragment.

**Open filing** hands the pane to a document reader — pages scroll continuously, more append
as you reach the end, and each page carries its own clip button, so choosing a page is a
decision made while reading it. The page the search matched is marked `search hit`.

Filing markdown is rendered rather than dumped: headings, GFM tables with right-aligned
figures, emphasis, lists. These pages are mostly financial tables, and as raw pipe-delimited
text they are unreadable. Parsing lives in `lib/markdown-parse.ts` as plain data so it can be
unit tested; the renderer only ever builds React elements, so OCR text from third-party PDFs
can never inject markup. It also unwraps the LaTeX artifacts the OCR emits, so `$51.60 \%$`
reads as `51.60 %`.

Page ids run consecutively within a filing (verified against production), so the next page is
the next id — but every fetched page is checked against the anchor's ticker, filing type, and
period, so a document ends rather than running into the next filing. Pages reached by reading
carry `bm25_score: 0`, because they were never ranked by the query.

## Scrapbooks

Assembling context is not a single sitting. Clips are saved to the browser as named
scrapbooks, switchable from the title in the header, so a researcher can go and do other work
and come back to the pile.

Only page identity and the researcher's own choices are stored — never page text. Full text is
refetched on open, which keeps storage small, keeps a reopened scrapbook consistent with the
corpus if a page has been republished, and avoids duplicating the corpus into localStorage. A
restored clip is marked loading until its text is back, so it can never silently contribute an
empty source.

**Storage is per-browser today.** That is a demo, not a workspace — see "Where this has to go"
below. In the meantime a pack can leave the machine: `Export all` writes a JSON file, `Import`
merges one back, and re-importing a file never overwrites work done since (the incoming copy
is re-identified). `Duplicate` copies a pack, which is how a quarterly evidence file gets
rolled into a new period.

The Send tab writes three files, because a workpaper has to reach the work:

| File | For |
|---|---|
| `<name>.md` | the analyst context, to paste into a memo or note |
| `<name>.json` | the machine payload, for an API caller or a model step |
| `<name>-sources.txt` | a numbered citation list whose numbers match `[S1]`, `[S2]` in a compiled answer |

## Where this has to go

Per-browser storage fails the workflow it is meant to serve: an analyst moves between machines,
hands evidence to a reviewer who asks "where did that number come from?", and returns to the
same pack next quarter. None of that survives a localStorage-only design.

The house pattern already exists in `psxgpt-serve`: Supabase for identity
(`apps/analysis/src/supabase.ts`, auth only — bearer token to user id) and Durable Object
SQLite for per-user storage. Adopting it would give scrapbooks the two properties they are
missing: they follow the user across devices, and they can be shared by link.

This app cannot do that yet — the pipeline repo holds no `SUPABASE_URL` or
`SUPABASE_SERVICE_ROLE_KEY`. Note also that the corpus Worker is read-only by construction, so
scrapbook writes belong in a separate Worker with its own binding rather than being bolted onto
it.

Set-level refinement, ported from the lane's post-retrieval shaping tools:

| Control | Why it matters |
|---|---|
| `dedupe adjacent` | Consecutive pages usually restate the same table. |
| `latest filing` | Keeps the newest period per company. |
| `cap 3 each` | Stops one company crowding a comparison. |

## Layout

Results and context sit side by side, so selecting a page never hides the results you were
reading and the running token count is always visible. Below 900px the context pane becomes
a sheet with a bottom bar. There are no stacked full-screen overlays.

## What the researcher sees per page

| Signal | Source |
|---|---|
| Indexed page summary | the pipeline's `search_text` column, not a model summary |
| Matched source text | FTS5 `snippet()`, hits delimited with `⟦ ⟧` |
| Rank and score | position plus `bm25()`, formatted so a real `4.1e-6` never prints as `0.00` |
| Open checks | `data_quality_flags`, scoped by ticker and filing period |
| Stored object | live R2 `head` on the selected source, so `available` means verified |

Each selected source can contribute either its indexed summary or its full page
markdown. The token estimate updates as that choice changes, because it is the
main lever on what the send actually costs.

## Compile

`POST /api/compile` sends the assembled markdown to one provider. Providers are
listed in `lib/providers.ts`, and only those with a key present in the
environment are offered — `/api/providers` reports availability without exposing
the key.

Compile lives in this app rather than in the corpus Worker on purpose: that
Worker is read-only and GET-only by construction and holds no model credentials.

The system prompt treats source text as untrusted evidence rather than as
instructions, which matters because the corpus is OCR'd text from third-party
PDFs.

## Local development

```bash
npm install
```

Copy `.env.example` to `.env` and fill in at least one provider key. The same
keys the pipeline uses are in the repo root `.env`.

```bash
npm run dev
```

## Validation

```bash
npm test
```

Unit tests cover query interpretation, the context markdown, score formatting, the proxy's
parameter-forwarding contract, the refinement operations, and the query-advice vocabulary.
Run `npm run test:build` to build first.

## Not yet wired

Authoritative-source pages are not searchable. `pipeline/01_authoritative_download.py`
downloads regulator and trade-body PDFs into `authoritative_documents/` with a
manifest, but nothing parses them into D1 or an FTS index yet. When that chain
lands, add the corpus id to `CORPORA` in `lib/corpus.ts` and to
`parseSearchFilters` in the corpus Worker; the rest of the UI is registry-driven
and needs no other change.
