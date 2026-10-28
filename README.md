# psxgpt-pipeline

PSX financial-data pipeline. Turns filings, PSX announcements, and macro reports into
audited, source-linked database rows and retrieval corpora:
**parse → review → extract → publish**, with deterministic QC gates and costed repair
triage between stages for the financial-statement spine.

Canonical design: [`PIPELINE_SPEC.md`](PIPELINE_SPEC.md).

> **Status: retired (October 2026).** psxGPT is no longer running. This pipeline is published as-is under the MIT licence for anyone who wants to learn from it or rebuild the corpus. Learnings remain at [psxgpt.com](https://psxgpt.com). No support or maintenance is offered.
>
> **Data is not included.** Filing PDFs, parsed pages, databases and other generated artifacts are not part of this repository. You download source documents yourself and are responsible for respecting their terms. To serve answers from the corpus, pair this with [psxgpt-serve](https://github.com/ishaheen10/psxgpt-serve). Copy `.env.example` to `.env` and fill in only the keys for the stages you run.

## Reading path

Start here for the short map, then read:

1. [`PIPELINE_SPEC.md`](PIPELINE_SPEC.md) for the stage-by-stage evidence contract.
2. The relevant stage artifact or report when a value, source page, ratio, or quality flag looks
   wrong.
3. The sibling `psxgpt-serve` docs only after the produced corpus row or source asset is known to
   be correct.

Each stage should make progress visible by recording what it consumed, what it emitted, what it
rejected, which checks passed or failed, and which downstream surface changed.

## Operating model

The pipeline builds evidence contracts for `psxgpt-serve`.

```text
source PDF / announcement / macro report
  -> parsed artifact
  -> review or retrieval metadata
  -> extraction or searchable text
  -> QC / derivation / publish
  -> D1 row, source asset, quality flag, or state-ledger entry
```

The pipeline's job is not just to run scripts. It must make every downstream answer
debuggable:

- **Source provenance** links published rows and retrieval pages back to PDFs or attachments.
- **Canonical metrics** turn filing labels into stable fields serve can query.
- **Basis and section** preserve consolidated vs unconsolidated context and ratio denominator
  meaning.
- **Stage artifacts** show what each step accepted, rejected, derived, or published.
- **Data-quality flags** carry scoped cautions for rows that are usable but need disclosure.
- **Step 19 state** reconciles statements, ratios, documents, assets, market data, and flags
  so cross-surface gaps are visible before serve users hit them.

When something is wrong, fix the earliest bad artifact and rerun downstream stages. Do not
hand-patch generated rows or compensate in serve for a publishing or extraction defect.

## The shape

Three model roles drive the financial-statement spine; everything else in that spine is
deterministic code and gates. Auxiliary corpora such as PSX announcements and macro reports
reuse parse-style model calls to build separate retrieval surfaces.

| Step | Script | What it does |
|------|--------|--------------|
| 01 | `01_ingest.py` | Coverage report on `database_pdfs/`; flag gaps |
| 02 | `02_parse.py` | **Gemini Flash Lite** — page PDF → markdown |
| 03 | `03_gate.py` | Classify pages: blank / urdu / thin / taggable |
| 04 | `04_tag.py` | **DeepSeek V4 Pro** — one call/page → named statement tag + basis |
| 05 | `05_fix.py` | Repair loop for parse failures flagged by gate |
| 06 | `06_route.py` | Deterministic routing: pick highest-confidence page per slot |
| 08 | `08_extract.py` | **DeepSeek V4 Pro** from markdown, with scoped Gemini-from-PDF repair mode — routed pages → ref-column markdown rows |
| 09 | `09_qc.py` | Formula reconciliation QC; appends failures to `artifacts/qc_failures.jsonl` |
| 10 | `normalize.py` | Jsonify raw extraction output → `data/json_{pl,bs,cf}/` |
| 11 | `derive.py` | Derive quarterly + LTM rows → `data/quarterly_{pl,cf}/`, `data/ltm_{pl,cf}/` |
| 12 | `flatten.py` | Flatten to `data/flat/{pl,bs,cf}.jsonl` |
| 13 | `13_publish.py` | Load flat rows into SQLite / D1 |
| 14 | `14_triage.py` | Diagnose failures, price fix cost, recommend action |
| 15 | `15_ratios.py` | Compute statement-derived ratios |
| 16 | `16_market_qc.py` / `16_scs_ratio_qc.py` | Market and external-reference QC |
| 17 | `17_publish_documents.py` | Publish filing-page text to `financial_documents_new` + FTS |
| 18 | `18_publish_assets.py` | Publish filing page PDFs/thumbnails to R2 |
| 19 | `19_pipeline_state.py` | Cross-stage readiness ledger + data quality flags |
| 20 | `20_publish_announcements.py` | Download ticker-specific PSX announcements, parse attachments with Gemini, publish FTS/R2 assets |
| M2 | `macro_push.py` | Publish already-parsed macro report pages to `macro_documents` + FTS/R2 assets |

Prompt packs live in `context/04_tag/` (tagging) and `context/08_{pl,bs,cf}/` (extraction).
Each pack has `rules.md` (procedure), `format.md` (output shape), and `fields.md` (canonical field list).

## Setup

```bash
uv sync
cp .env.example .env   # fill GEMINI_API_KEY and DEEPSEEK_API_KEY
npx wrangler login     # only for D1 uploads
```

## Running the pipeline

Each step is a standalone script. Run them in order:

```bash
python3 pipeline/02_parse.py --ticker HBL --year 2024
python3 pipeline/03_gate.py  --ticker HBL --year 2024
python3 pipeline/04_tag.py   --ticker HBL --year 2024
python3 pipeline/06_route.py --ticker HBL --year 2024
python3 pipeline/08_extract.py --type pl --ticker HBL
python3 pipeline/09_qc.py    --type pl
python3 pipeline/10_normalize.py --type pl
python3 pipeline/10_normalize.py --type bs
python3 pipeline/10_normalize.py --type cf
python3 pipeline/11_derive.py --type pl
python3 pipeline/11_derive.py --type cf
python3 pipeline/12_flatten.py --type pl
python3 pipeline/12_flatten.py --type bs
python3 pipeline/12_flatten.py --type cf
python3 pipeline/13_publish.py --db psx_test.db --ticker HBL
python3 pipeline/14_triage.py
```

Pass `--help` to any script for full options.

## Key artifacts

| Path | Contents |
|------|----------|
| `database_pdfs/` | Source filing PDFs (gitignored) |
| `markdown_pages/` | Per-page parse output |
| `review/` | Per-filing tag + basis jsonl |
| `data/extracted_*/` | Raw extraction output |
| `data/flat/` | Flattened rows ready for upload |
| `announcement_documents/`, `announcement_markdown/` | PSX announcement attachments and Gemini markdown |
| `macro_documents/`, `macro_pages/`, `macro_markdown/`, `macro_thumbnails/` | Macro source PDFs, split pages, parsed markdown, and previews |
| `artifacts/qc_failures.jsonl` | All QC failures (all stages append here) |
| `artifacts/reports/` | Per-step JSON reports |

## Test / local DB

```bash
python3 pipeline/13_publish.py --db psx_test.db --ticker AABS
sqlite3 psx_test.db 'SELECT ticker, COUNT(*) FROM financial_statements GROUP BY ticker'
```

Production upload requires `wrangler` and targets D1 database `psx`
(id `6cd31b68-0737-4d45-a38b-cb225c2f247e`, see `wrangler.toml`).

## Published database surfaces

The served database is broader than the statement spine:

| Surface | D1 tables | Source assets | Purpose |
|---------|-----------|---------------|---------|
| Structured statements | `financial_statements`, `ratios`, `management_comp`, `market_data`, `data_quality_flags` | Filing page provenance via `source_pages` / `source_url` | Numeric answers, models, ratios, and scoped cautions |
| Filing pages | `financial_documents_new`, `filings_fts_new` | `PDF_PAGES/...`, `PDF_THUMBNAILS/...` | Annual/quarterly report narrative, notes, schedules, and source previews |
| PSX announcements | `psx_announcements`, `psx_announcements_fts` | `ANNOUNCEMENTS/...`, `ANNOUNCEMENT_THUMBNAILS/...` | Ticker-specific exchange notices and corporate announcements |
| Macro reports | `macro_documents`, `macro_fts` | `MACRO_PAGES/...`, `MACRO_THUMBNAILS/...` | Budget, Economic Survey, and SBP annual report pages |

Do not mix these corpora. Filing pages, announcements, and macro reports have separate local
folders, D1 tables, FTS indexes, and source-asset namespaces because serve uses those
boundaries to route retrieval and group sources in the UI.

## Where to go next

- For the complete stage contract, read [`PIPELINE_SPEC.md`](PIPELINE_SPEC.md).
- For a broken published answer, inspect the earliest pipeline artifact that could have produced
  the bad row, page, ratio, flag, or asset; rerun downstream stages after fixing it.
- For route selection, SQL, deterministic request-time compute, analyst wording, traces, or UI
  source grouping, switch to the sibling `psxgpt-serve` repo.
