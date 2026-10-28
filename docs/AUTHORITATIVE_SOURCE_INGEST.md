# Authoritative PDF ingestion

## Boundary

This collector covers institutions that publish authoritative documents on websites but do
not expose a stable public document API. It does not replace the PSX listed-company filing
collector in `pipeline/01_download.py`, and it does not put regulator or trade-body reports
under `database_pdfs/`.

The first observable handoff is:

```text
official publication page
  -> config/authoritative_sources.json
  -> pipeline/01_authoritative_download.py
  -> authoritative_documents/{country}/{source}/{collection}/{year}/
  -> artifacts/01_authoritative_download/manifest.json
  -> artifacts/reports/01_authoritative_download.json
  -> artifacts/01_authoritative_download/discovery.json
  -> artifacts/01_authoritative_download/catalog.json
```

Every later parse, page-split, search, or publish step should read the manifest. Folder
presence alone is not a provenance contract.

`pipeline/01_authoritative_organize.py` materializes a rebuildable, human-readable symlink
view under `authoritative_corpus/{country}/{source}/{collection}/{year}/`. This keeps names
pleasant for browsing without renaming the immutable content-addressed originals or breaking
already-published R2 keys.

The second observable handoff mirrors the existing financial-statement asset contract:

```text
artifacts/01_authoritative_download/manifest.json
  -> pipeline/02_authoritative_assets.py
  -> authoritative_pages/{country}/{source}/{collection}/{year}/{document}/page_###.pdf
  -> authoritative_thumbnails/{same path}/page_###.jpg
  -> artifacts/02_authoritative_assets/manifest.json
  -> R2 AUTHORITATIVE_DOCUMENTS/ + AUTHORITATIVE_PAGES/ + AUTHORITATIVE_THUMBNAILS/
```

`02_authoritative_assets.py` hashes each original against the download manifest, opens the
PDF, and proves that every expected page PDF and thumbnail exists. R2 upload is gated behind
`--upload-assets`. Public `source_url` and `thumbnail_url` fields remain null until the
corresponding key has been uploaded successfully.

The searchable handoff is incremental and page-based:

```text
artifacts/01_authoritative_download/manifest.json
  -> pipeline/03_authoritative_parse.py
  -> authoritative_markdown/{country}/{source}/{collection}/{year}/{document}/page_###.md
  -> artifacts/03_authoritative_parse/manifest.json
  -> artifacts/03_authoritative_parse/search.sqlite
```

Each FTS row retains publisher, source, collection, document title, canonical publisher URL
and the R2 page URL when that page has been published. Pages with too little extractable text
are marked `needs_ocr`; they are not filled with guessed text or placed in the search index.

## Why this is separate from the existing Step 01

`pipeline/01_download.py` encodes one PSX UI: years, ticker rows, download modals, annual and
quarterly naming, and the `database_pdfs/{ticker}/{year}` contract. Those assumptions are
correct for company filings and incorrect for an OGRA price notification, a NEPRA industry
report, or a trade-body annual report.

The generic collector instead standardizes:

- source and country identity;
- official-domain allowlists;
- collection-specific landing pages and link rules;
- immutable content-addressed PDF files;
- conditional HTTP requests when the publisher supplies ETag or Last-Modified;
- URL and SHA-256 provenance;
- source health that distinguishes unchanged, failed, and blocked runs; and
- explicit TLS verification state.

Website-specific differences remain small configuration entries. A custom adapter should be
added only when the publication page cannot be described as links on a page.

## Pakistan seed

The initial registry includes:

| Source | Collections | Current access behavior |
|---|---|---|
| Pakistan Stock Exchange | PSX's own financial, quarterly, sustainability, and ESG reports | Direct HTML/PDF |
| NEPRA | State of Industry and annual reports | Direct HTML/PDF |
| OGRA | Notifications and annual reports | Cloudflare currently blocks both HTTP and headless Chromium; retained in the registry but disabled from the green cron set |
| Pakistan Poultry Association | Annual reports | The official site currently has a hostname-mismatched TLS certificate; this source is explicitly marked `verify_tls: false` and the landing page uses HTTP |

The first expansion wave adds FBR yearbooks, SECP annual reports, CCP annual reports,
Planning Commission annual plans, TDAP research reports, PAMA production/sales reports and
SBP core reports to the enabled refresh. PBS publications and Ministry of Finance surveys
and budgets remain registered but disabled from the green refresh while their much larger
historical collections advance through bounded `--new-only` batches.

The cumulative catalog is the scope ledger. At the 2026-07-27 checkpoint it contained 715
first-party identities across 12 publishers; 232 had already been downloaded into the
immutable manifest. These counts are expected to rise with each scheduled batch.

OGRA should not be silently scraped through an unofficial mirror. The next OGRA adapter
should use a stable first-party publication feed if one becomes available, or a browser
session permitted by OGRA. Until then, it remains disabled with an explicit reason in the
registry and should be probed separately from the reliable scheduled refresh.

## Commands

Discover without downloading:

```bash
.venv/bin/python pipeline/01_authoritative_download.py --country PK --dry-run
python3 run.py --chain authoritative --country PK --dry-run
```

Refresh all enabled Pakistan sources:

```bash
.venv/bin/python pipeline/01_authoritative_download.py --country PK
```

Verify and prepare the downloaded corpus locally:

```bash
.venv/bin/python pipeline/02_authoritative_assets.py --country PK
```

Publish only after that local verification is green:

```bash
.venv/bin/python pipeline/02_authoritative_assets.py --country PK --upload-assets
```

Reconcile the local upload ledger against the objects actually present in R2:

```bash
.venv/bin/python pipeline/02_authoritative_assets.py --country PK --verify-r2
```

The combined repo-native command downloads, verifies, splits, thumbnails, and—only when
explicitly requested—uploads:

```bash
python3 run.py --chain authoritative --country PK --since-year 2025
python3 run.py --chain authoritative --country PK --since-year 2025 --upload-assets
```

Refresh only recent dated documents while still retaining undated publication links:

```bash
.venv/bin/python pipeline/01_authoritative_download.py --country PK --since-year 2025
```

Iterate through a large candidate source without revisiting stored identities:

```bash
.venv/bin/python pipeline/01_authoritative_download.py \
  --source pk_pbs --include-disabled --new-only --max-documents 20
```

If the publisher exposes a stale link at the front of the remaining candidate list, use
`--offset-documents` for a bounded diagnostic run, then record a source-specific exclusion
only after confirming the first-party URL is persistently dead.

Inspect proposed names and folders before downloading. `discovery.json` is the latest run;
`catalog.json` accumulates identities across iterative source runs:

```bash
jq '.documents[] | {source_id, title, publication_year, proposed_folder, proposed_filename}' \
  artifacts/01_authoritative_download/discovery.json
```

Refresh the readable folder view after a backfill:

```bash
.venv/bin/python pipeline/01_authoritative_organize.py --country PK
```

Build the page-text corpus in resumable batches:

```bash
.venv/bin/python pipeline/03_authoritative_parse.py --country PK --max-documents 10
```

Search the local corpus:

```bash
.venv/bin/python pipeline/03_authoritative_parse.py --query '"power tariff"' --limit 10
python3 run.py --chain authoritative_search --country PK --max-documents 10
```

Validate one source with a bounded download:

```bash
.venv/bin/python pipeline/01_authoritative_download.py \
  --source pk_nepra \
  --collection state_of_industry \
  --max-documents 1
```

List the configured sources:

```bash
.venv/bin/python pipeline/01_authoritative_download.py --list-sources
```

The process exits non-zero when any source is blocked or fails. This is intentional: cron
must not turn a broken publisher page into a false “no new documents” success.

## Scheduling

Use one daily scheduler entry for the country, not one cron definition per website:

```bash
cd /Users/isfandiyarshaheen/psxgpt-pipeline
.venv/bin/python pipeline/01_authoritative_download.py --country PK
```

The active daily task refreshes every enabled collection without a year filter. Some valid
collections publish infrequently, so a recent-year gate would create false zero-discovery
failures. Conditional requests, hashes and the manifest keep that refresh incremental.
Large disabled candidates and historical gaps advance separately with bounded `--new-only`
batches.
The per-collection `cadence` in the registry is descriptive for now. A future scheduler can
honor it without changing collector code.

The scheduled task should always inspect:

- `artifacts/reports/01_authoritative_download.json`;
- `artifacts/01_authoritative_download/manifest.json`; and
- whether `downloaded + updated` is plausible relative to `discovered`.

## Adding Pakistan sources

Add sources in this order:

1. regulators and statutory bodies: SECP, SBP, PBS, CCP, PTA, DRAP;
2. federal economic publications: Ministry of Finance, Planning Commission, Commerce, FBR;
3. market infrastructure: NCCPL, CDC, PMEX;
4. sector authorities: PPRA, PPIB, AEDB, Indus River System Authority and provincial regulators;
5. recognized trade bodies: poultry, cement, fertilizer, autos, textiles, banks, insurance, oil marketing and pharmaceuticals.

For each collection, verify the official publisher/domain, choose the exact publication
page, bound the link patterns, run `--dry-run`, then download one PDF and inspect its manifest
record before enabling the full backfill.

## Adding other countries

Country expansion is data, not a fork. Add a new ISO country code and source entries to the
same registry. Keep one source ID per institution and one collection ID per publication
family. Do not merge similarly named regulator reports across countries; country, publisher,
collection, canonical URL, and content hash remain part of identity.
