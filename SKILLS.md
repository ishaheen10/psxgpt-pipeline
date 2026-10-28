# psxGPT Pipeline Operating Guide

This repo produces the data consumed by `psxgpt-serve`. Operate it from artifacts and reports, not from assumptions.

## Working Rules

- Do not make band-aid fixes. Start from the failing artifact, D1 gap, QC report, or reproducible ticker example and identify the root cause before editing code.
- Every stage must emit an artifact that explains what changed, what is missing, and why. If a stage only prints counts, improve the stage before relying on it.
- After every rerun, inspect `artifacts/reports/*.json` and the stage-specific artifacts before claiming the issue is fixed.
- Coverage gaps must be explained in data terms: missing source pages, missing prior annuals, missing prior same-duration periods, missing numerator/denominator fields, zero denominators, missing same-date balance sheet joins, or true net losses. Do not collapse these into “coverage is thin.”
- Prefer rerunning the pipeline stage boundary over hand-patching downstream tables. If D1 is wrong, fix the producing stage, regenerate artifacts, push through the normal D1 step, then rebuild dependent stages.
- Treat manifests as contracts. If a downstream stage is missing data, compare its input manifest/report to its output report before changing logic.
- Keep generated SQL and D1 state in sync with source artifacts. Rebuild ratios after any PL/BS/CF statement push that can affect ratio inputs.
- Treat pipeline QC as the eval suite. A change is not fixed because a script completed; it is fixed when the relevant artifact/report explains the before/after state and the downstream serving query no longer reproduces the failure.
- Avoid serve-side band-aids for data-publishing problems. Multi-minute D1 import locks must be fixed in the D1 push/publish pattern, not by widening serve retry budgets.
- Public corpus, ratio, coverage, or reliability changes need a changelog entry in `psxgpt-serve/changes/`, followed by `node scripts/sync-changelog.mjs` in `psxgpt-serve`. The public changelog is generated there; do not hand-edit generated changelog files.

## Operating Loop

Use this loop when a user query or serve trace points back to data quality, coverage, or publishing:

For any broad or long-running goal, first copy
`docs/goal_run/RUN_STATE_TEMPLATE.md` to a dated file under `artifacts/reports/`
and keep it updated as the source of truth for current hypothesis, issue category,
commands, production changes, and handoff. Use
`docs/goal_run/ISSUE_OUTPUT_CONTRACT.md` when changing or interpreting artifacts that
drive repair work.

1. **Start from serve evidence.** Capture the failing query, run ID when available, affected ticker/table, lane result, SQL, and source-page or statement rows that looked wrong.
2. **Classify the failure.** Pick one primary category before editing code:
   `extraction_or_arithmetic_defect`, `source_report_caveat`,
   `stale_or_missing_market_data`, `scs_reference_basis_drift`,
   `d1_publish_or_artifact_sync`, `serve_retrieval_or_reasoning_failure`,
   `out_of_universe_or_user_scope`, or `transient_provider_or_runtime`.
3. **Map the failure to a pipeline boundary.** Decide whether the likely source is route, normalize, derive, flatten, D1 push, ratios, or market/metadata QC. Do not edit a downstream artifact until the producing stage has been inspected.
4. **Reproduce with the smallest pipeline command.** Prefer a single ticker/type/stage command. Keep the command in the handoff so the case can become a recurring eval/QC check.
5. **Fix the earliest bad artifact.** If normalized JSON is wrong, fix normalize or upstream extraction. If flattened rows are wrong but normalized rows are correct, fix flatten. If D1 is wrong but generated SQL is correct, fix the push/publish path.
6. **Rerun downstream stages in order.** Inspect reports and residual gaps before pushing data to D1.
7. **Record the update.** Write or update a local markdown report under `artifacts/reports/` or the relevant investigation location with before/after artifacts, commands, files changed, reports inspected, D1 tables refreshed, and residual risk.
8. **Verify in serve.** After a D1-affecting change, go to `psxgpt-serve`, run the focused replay or eval on `psxgpt-serve-test`, and confirm `/api/admin/issues` does not show new pipeline-induced failures.
9. **Update the public changelog.** If users will see changed data coverage, ratios, statement extraction, source-page reliability, D1 publishing behavior, or serving reliability, add a change record in `psxgpt-serve/changes/` and regenerate the changelog.

Pipeline operation is not complete until the producing artifact is correct and the consuming serve query behaves correctly.

## Standard Stage Review Flow

1. Read the relevant source stage and current artifacts.
   - Route manifest: `artifacts/06_route/manifest.json`
   - Normalize JSON: `data/json_{pl,bs,cf}/`
   - Derive QC: `artifacts/11_derive/qc_{pl,cf}.json` and `artifacts/11_derive/ltm_{pl,cf}_qc.json`
   - Flatten reports: `artifacts/reports/12_flatten_{pl,bs,cf}.json`
   - D1 push report: `artifacts/reports/13_d1_push.json`
   - Ratio coverage: `artifacts/15_ratios/coverage_report.json` and `artifacts/reports/15_ratios.json`
2. Reproduce with the smallest scoped command, usually `--ticker` or `--type`.
3. Fix the earliest stage whose output is wrong.
4. Rerun downstream stages in order.
5. Compare before/after counts and explain residual gaps by reason, not just by ticker count.
6. If D1 or serving behavior was affected, verify in `psxgpt-serve` with a focused deployed replay against `psxgpt-serve-test`.

## Eval and Fix Workflow

Use artifact gates in the producing repo, then serving replays in the consuming repo:

1. **Scope the failure to the earliest bad artifact.** Start with the failing ticker/report/table, then inspect the nearest manifest/report before editing code.
2. **Run the smallest stage command that reproduces the issue.** Prefer `python3 run.py --chain <chain> --ticker <TICKER> --only <stage>` or a direct `python3 pipeline/<stage>.py --type <pl|bs|cf> --ticker <TICKER>` command where supported.
3. **Fix the earliest stage whose output is wrong.** Do not patch flattened JSON, SQL, or D1 rows if route/normalize/derive produced the wrong source artifact.
4. **Rerun downstream stages in order.** For PL/CF changes this usually means normalize -> derive -> flatten -> D1 push -> ratios. For BS changes it usually means normalize -> flatten -> D1 push -> ratios.
5. **Inspect reports before claiming success.** At minimum, read the relevant files under `artifacts/reports/`, stage-specific QC output, and ratio coverage when ratios are affected.
6. **Verify downstream serving behavior.** In `psxgpt-serve`, replay the original user query or a focused eval against `psxgpt-serve-test` and confirm the answer/trace uses the corrected data.

Pipeline fixes should report:

- failing artifact or D1 row before the fix,
- exact command used to reproduce,
- files changed,
- exact rerun commands,
- reports inspected,
- D1 tables refreshed,
- serving replay or eval used as downstream verification.

If the downstream verification needs a token, use the serve repo token handling rules: load `ADMIN_TOKEN` from `psxgpt-serve/.admin-token.local` without printing it, or use `EVAL_TOKEN` only for eval-script access when admin access is unavailable.

## Common Commands

Normalize and derive flow statements:

```bash
python3 pipeline/10_normalize.py --type pl
python3 pipeline/10_normalize.py --type cf
python3 pipeline/11_derive.py --type pl
python3 pipeline/11_derive.py --type cf
python3 pipeline/12_flatten.py --type pl
python3 pipeline/12_flatten.py --type cf
```

Flatten balance sheet when balance-sheet fields or ratio denominators changed:

```bash
python3 pipeline/10_normalize.py --type bs
python3 pipeline/12_flatten.py --type bs
```

Push refreshed statements and rebuild ratios:

```bash
python3 pipeline/13_d1_push.py --type pl
python3 pipeline/13_d1_push.py --type cf
python3 pipeline/13_d1_push.py --type bs
python3 pipeline/15_ratios.py
```

### Source corpus: documents + thumbnails + R2 assets

`financial_documents_new` (+ `filings_fts_new` FTS) and the page PDFs/thumbnails on
`source.psxgpt.com` are published **separately from the statement spine**. `13_d1_push.py`
and `15_ratios.py` publish statements/ratios; they do NOT touch the document corpus. After a
universe expansion or refresh you must also run the document/asset flow, or you get numeric
answers with no source panel and filings that FTS cannot retrieve.

Full publish for a new/refreshed ticker (one chain does thumbnails → D1 docs → R2 assets):

```bash
python3 run.py --chain documents --ticker FECTC --push-d1 --upload-assets
```

The chain runs `17_publish_documents --push-d1` (refreshes `financial_documents_new` +
`filings_fts_new`) then `18_publish_assets` (generates thumbnails 360px/q70/110dpi and uploads
page PDFs + thumbnails to R2 via the **S3 API**, 150 workers, manifest-skips already-uploaded).
Without `--push-d1` it is SQL-only; without `--upload-assets` the asset step is skipped.

Whole-universe / gap operations:

```bash
python3 pipeline/17_publish_documents.py --gap --push-d1        # close D1 doc gap (vs financial_statements)
.venv/bin/python pipeline/18_publish_assets.py --tickers FECTC           # thumbnails + R2 upload for a scoped ticker
.venv/bin/python pipeline/18_publish_assets.py --all                     # whole corpus (manifest skips uploaded keys)
.venv/bin/python pipeline/18_publish_assets.py --tickers FECTC --thumbnails-only   # just (re)build thumbnails
```

Do not use `18_publish_assets.py --gap` after `17_publish_documents.py --gap --push-d1`; once D1 document rows exist, the document gap is closed and asset `--gap` has no target tickers. Use the same ticker list you just published, or `--all` when relying on the local upload manifest.

Step 18 needs repo environment dependencies (`pdf2image`, `Pillow`, `boto3`, and Poppler). Prefer `python3 run.py --chain documents ...`; if running step 18 directly, use `.venv/bin/python`.

Verify a ticker is fully published:

```bash
pnpm --dir ../psxgpt-serve/apps/analysis exec wrangler d1 execute psx --remote --json --command "SELECT COUNT(*) AS docs FROM financial_documents_new WHERE ticker='FECTC'; SELECT COUNT(*) AS fts_rows FROM filings_fts_new f JOIN financial_documents_new d ON d.id=f.rowid WHERE d.ticker='FECTC';"
curl -I https://source.psxgpt.com/PDF_PAGES/FECTC/2025/FECTC_Annual_2025/page_055.pdf
curl -I https://source.psxgpt.com/PDF_THUMBNAILS/FECTC/2025/FECTC_Annual_2025/page_055.jpg
```

Do not treat a ticker as fully published if `financial_statements` rows exist but
`financial_documents_new`, `filings_fts_new`, or source-host assets are missing.

### Macro corpus folders

Macro sources are a separate auxiliary corpus and must not live under the company filing
roots. Keep the local corpus in:

- `macro_documents/{source_doc}/...pdf` for original macro PDFs
- `macro_pages/{source_doc}/{chapter}/page_NNN.pdf` for split pages
- `macro_markdown/{source_doc}/{chapter}/page_NNN.md` for Gemini markdown
- `macro_thumbnails/{source_doc}/{chapter}/page_NNN.jpg` for page thumbnails
- `macro_review/{source_doc}/{chapter}.jsonl` for review/tag evidence

`pipeline/macro_push.py` reads from `macro_markdown/` and publishes the combined table/text
retrieval surface to `macro_documents` + `macro_fts` in D1. It keeps using the historical
`macro/...` keys when consulting `artifacts/03_gate/skip_manifest.json`.

Publish macro source assets after D1 so serve can show clickable source pages:

```bash
.venv/bin/python pipeline/macro_push.py --assets-only --workers 50
curl -I https://source.psxgpt.com/MACRO_PAGES/economic_survey_2025/7_Inflation/page_004.pdf
curl -I https://source.psxgpt.com/MACRO_THUMBNAILS/economic_survey_2025/7_Inflation/page_004.jpg
```

Serve queries this corpus through the dedicated `macro_documents` lane. Do not mix macro
rows into company filing pages or announcement rows.

### PSX announcements corpus

Ticker-specific PSX corporate announcements are a separate unstructured corpus from filing
pages. They publish to `psx_announcements` + `psx_announcements_fts`, with downloaded source
files stored under `announcement_documents/{ticker}/{year}/`. The step is deterministic: it
uses PSX's corporate-announcements JSON feed, resolves the site's JavaScript file redirect to
the stable `/psx/files` or `/psx/files-attachment` byte endpoint, downloads every attachment
format PSX exposes, parses each attachment to markdown under
`announcement_markdown/{ticker}/{year}/`, and publishes a bounded markdown excerpt to D1. The parser
uses Gemini 3.1 Flash Lite (`gemini-3.1-flash-lite` by default), matching the financial-page
parse path. Do not add deterministic PDF/OCR fallbacks for announcements; if Gemini fails,
the markdown artifact should say so explicitly and the report should count `gemini_failed`.
Gemini failure placeholders are local audit artifacts only; they are skipped from generated
SQL unless `--include-parse-failures` is explicitly passed.

This is text retrieval only. Do not add statement extraction, normalization, arithmetic QC, or
repair loops here. The BM25 `search_text` should be compact, like `financial_documents_new`:
ticker/date metadata tags, notice status, title, and a short discovery summary. Do not include
a long body excerpt in `search_text`. The full Gemini extract stays in `announcement_markdown/`;
D1 `text` is intentionally bounded to avoid oversized SQL statements.

Follow the same operating shape as the filing-page corpus: parse/materialize artifacts first,
then publish from artifacts. First focus on file and markdown coverage. Download all
announcement attachments for the scoped date window, parse them to markdown, and inspect the
report before generating SQL or touching D1:

```bash
.venv/bin/python pipeline/20_publish_announcements.py --start-date 2026-01-01 --end-date 2026-06-24 --download-only --parse-markdown --workers 20
python3 run.py --chain announcements --start-date 2026-01-01 --end-date 2026-06-24 --download-only --parse-markdown --workers 20
```

The download-only report must show the feed rows seen, duplicate skips, download failures,
format counts via `content_type`/local paths, local paths under `announcement_documents/`,
markdown paths under `announcement_markdown/`, and markdown parse methods. If the report
shows `gemini_failed`, retry the same parse-only command first. Any persistent failures
should be captured in a report such as
`artifacts/reports/20_publish_announcements_parse_coverage.md`; do not publish those failed
placeholder bodies to D1. Only after markdown coverage is understood should you generate rows,
push the same scope, and publish source assets for user-facing source previews:

```bash
.venv/bin/python pipeline/20_publish_announcements.py --start-date 2026-01-01 --end-date 2026-06-24 --push-d1
.venv/bin/python pipeline/20_publish_announcements.py --start-date 2026-01-01 --end-date 2026-06-24 --download-only --upload-assets --workers 50
python3 run.py --chain announcements --start-date 2026-01-01 --end-date 2026-06-24 --push-d1 --upload-assets
```

For small spot checks, use an explicit ticker/date scope:

```bash
.venv/bin/python pipeline/20_publish_announcements.py --ticker MARI --date 2026-06-24
.venv/bin/python pipeline/20_publish_announcements.py --ticker MARI --date 2026-06-24 --push-d1
```

For ongoing ingestion after the backfill, run a short overlapping window so late-posted PSX
announcements are picked up idempotently:

```bash
.venv/bin/python pipeline/20_publish_announcements.py --days 3 --universe-only --download-only --parse-markdown --reset-markdown --workers 3
```

This announcement ingest is separate from new financial filing ingestion. New annual/quarterly
filings still start with the existing financials downloader and document chain, for example:

```bash
python3 pipeline/01_download.py --start-year 2026
python3 run.py --chain documents --ticker <TICKER> --push-d1 --upload-assets
```

Verify after a push:

```bash
pnpm --dir ../psxgpt-serve/apps/analysis exec wrangler d1 execute psx --remote --json --command "SELECT ticker, announcement_date, announcement_time, title FROM psx_announcements WHERE ticker='MARI' ORDER BY announcement_date DESC, announcement_time DESC LIMIT 5;"
pnpm --dir ../psxgpt-serve/apps/analysis exec wrangler d1 execute psx --remote --json --command "SELECT a.ticker, a.announcement_date, a.title FROM psx_announcements_fts f JOIN psx_announcements a ON a.id=f.rowid WHERE psx_announcements_fts MATCH 'MARI briefing' LIMIT 5;"
curl -I https://source.psxgpt.com/ANNOUNCEMENTS/MARI/2026/MARI_2026-06-24_13_50_38_279075.pdf
curl -I https://source.psxgpt.com/ANNOUNCEMENT_THUMBNAILS/MARI/2026/MARI_2026-06-24_13_50_38_279075.jpg
curl -I https://source.psxgpt.com/ANNOUNCEMENTS/UBL/2026/UBL_2026-06-24_14_49_22_279083.gif
```

Do not load announcements into `financial_documents_new`; that table is only for filing
pages with page-level source previews. Serve announcement retrieval must use the dedicated
`psx_announcements` lane and should surface `ANNOUNCEMENTS/...` assets as announcement
sources, not filing pages. Announcement assets are not PDF-only: PSX also publishes GIF/image
notices, and the asset publisher must upload every local file under `announcement_documents/`
so D1 rows never fall back to direct PSX URLs.

**R2 credentials (the trap that wastes hours):** `18_publish_assets.py` authenticates with the
R2 **S3 token** credentials in `.env` — `CLOUDFLARE_R2_ACCESS_KEY_ID` / `CLOUDFLARE_R2_SECRET_ACCESS_KEY`
/ `CLOUDFLARE_R2_ENDPOINT` (create at Cloudflare → R2 → Manage R2 API Tokens → "Object Read &
Write"). A `cfat_`/`cfut_` API-token *value* is NOT an S3 credential — it only works against
the rate-limited Cloudflare REST API (~1,200 req/5min), which stalls bulk uploads and starves
concurrent D1 `wrangler` pushes. Always use the S3 endpoint for bulk uploads. See `.env.example`.

### Broken (garbled) markdown pages

Gemini parse can loop and emit a huge garbled page (text only — `search_text` stays sane), which
bloats `financial_documents_new.text` and can hit `SQLITE_TOOBIG`. Detect + re-parse, then
re-publish the affected tickers (no re-tag needed):

```bash
python3 pipeline/02_parse.py --reparse-broken --max-repeat 100   # re-parse; affected tickers in artifacts/reports/02_parse.json
# then re-publish each affected ticker: python3 pipeline/17_publish_documents.py --ticker <T> --push-d1
```

Use `--ticker SYMBOL` for scoped debugging where the stage supports it.

Runner equivalents:

```bash
python3 run.py --chain pl --ticker HBL
python3 run.py --chain bs --ticker HBL
python3 run.py --chain cf --ticker HBL
python3 run.py --chain ratios
python3 run.py --chain market --ticker HBL --push-d1
python3 run.py --chain market_qc --ticker HBL
```

### Market data: close + market cap

`pipeline/16_market_data.py` is the canonical, self-contained market-data publisher. Do not
pull market rows from legacy `psxgptinfra` or `psxgptinfrav3` scripts. The PSX DPS EOD row
shape is `[timestamp, close, volume, open]`; use index `1` as `closing_price`, not index `3`.

Market data is authoritative for only:

- `closing_price`
- `total_shares`
- `market_cap = closing_price * total_shares`

Legacy physical columns such as `pe_ratio`, `pb_ratio`, `dividend_yield`, and
`enterprise_value` are cleared by the market-data refresh. Compute those values from
market cap plus sourced financial statement rows when needed.

Scoped refresh and QC:

```bash
python3 run.py --chain market --ticker FECTC --push-d1
```

Whole-universe refresh:

```bash
python3 run.py --chain market --push-d1
```

Use low concurrency for PSX. The default is intentionally one worker with retries/backoff;
do not raise `--workers` for the whole universe unless you are prepared to handle PSX
empty-reply throttling. For a gap batch, use a comma-separated list:

```bash
python3 pipeline/16_market_data.py --tickers AASM,DSFL,FNBM --push-d1
```

SQL-only rehearsal:

```bash
python3 pipeline/16_market_data.py --ticker FECTC
python3 pipeline/16_market_qc.py --ticker FECTC
```

After a market-data refresh, inspect:

```bash
cat artifacts/reports/16_market_data.json
cat artifacts/reports/16_market_qc.json
cat artifacts/16_market_qc/market_qc.json
cat artifacts/reports/16_scs_ratio_qc.json
cat artifacts/16_scs_ratio_qc/scs_ratio_qc.md
```

The QC compares D1 `closing_price`, `total_shares`, and `market_cap` against live PSX
DPS/company-page inputs.

If `16_market_qc.py` reports `psx_latest_row_is_stale`, treat that as a warning-level
data-quality condition, not a green pass. Step 19 converts those rows into
`stale_psx_market_price` warnings and serve-consumable `market_data` quality flags. Keep
the raw D1 market row for audit, but do not treat stale-price tickers as clean priced
coverage for price-dependent user answers.

### SCS P/E + dividend-yield reference check

Use SCS only as an external spot-check reference for valuation ratios; do not use it to
overwrite PSX close/market-cap rows or parsed financial-statement values.

```bash
python3 run.py --chain scs_ratio_qc
python3 run.py --chain scs_ratio_qc --ticker ALTN
python3 run.py --chain scs_ratio_qc --scs-only
python3 run.py --chain scs_ratio_qc --include-market-only  # intentional legacy market-only audit
```

By default this compares the served production D1 statement/ratio corpus so generated
quality flags line up with the health matrix. It intentionally excludes market-data-only
legacy symbols that are not served as corpus tickers. Use `--include-market-only` only
when auditing raw legacy market rows outside the served corpus. The script also pulls
SCSTrade market-valuation rows for the external-reference subset:

- Market Valuations all-market feed
- Sector valuation feeds
- Top 40 lowest P/E stocks
- Top 40 highest dividend-yield stocks

It then computes direct values from production D1:

- `P/E = latest market_cap / latest annual_or_LTM net_profit`
- dividend-yield candidates from latest legacy `market_data.dividend_yield` and
  `cash_flow.dividends_paid / market_cap`, each labeled by basis
- every comparable annual/LTM net-profit candidate by period, section, and unit scale
- SCS-implied declared dividend per share as `SCS price * SCS dividend_yield / 100`

Do not reconstruct P/E through historical shares outstanding, and do not select a net-profit
row because it is closest to SCS. If SCS differs materially, use the report to identify
whether the issue is the selected net-income period, statement section, or reported unit
scale. SCS is a sanity check, not the production source of truth.
Use `--scs-only` only for quick spot checks against the currently available SCS rows; it is
not a complete corpus health check.

User-facing SCS comparison rows are cautions, not extraction blockers. P/E rows that meet
the publication rule should be written as `warning` severity in
`artifacts/16_scs_ratio_qc/data_quality_flags.jsonl`; reserve `blocker` severity for hard
statement arithmetic/source failures from the deterministic QC stages. The public/admin
health grid should show user-facing SCS differences as yellow, never red.
The operator SCS report may include broad 25% reference differences, but production
`data_quality_flags` should publish only actionable P/E issues: missing market cap, missing
annual/LTM net profit, or a severe positive direct-vs-SCS P/E divergence of at least 3x.
If SCS is explained by another clean reported-scale net-profit candidate, such as an older
annual or different section basis within 50% of the SCS P/E, keep it in the operator report
as reference-basis drift and do not publish a user-facing caution.
Do not publish a caution just because latest annual/LTM net profit is non-positive; that is
a valid reason P/E is not meaningful unless the statement row itself is QC-flagged upstream.
Do not publish a user-facing SCS P/E flag when both direct P/E and SCS P/E are missing, or
when SCS P/E is `0.0` and D1 can compute a positive market-cap/net-income P/E. Those are
non-comparable external-reference rows, not failed comparisons. Keep the notes in the SCS
operator report, but only publish a P/E caution when SCS has a positive P/E reference, market
cap is missing, or the direct-vs-reference comparison materially differs.

Do not treat legacy `market_data.dividend_yield` as authoritative. It is a deprecated
published column and may be stale or implausible. Cash-flow `dividends_paid / market_cap`
is also only a proxy because it is a lagged cash movement, not the declared
dividend basis used by market sites, but it is the only direct D1 candidate eligible
for operator comparison. Keep legacy market-data dividend yield in operator reports as
a labeled candidate only; never select it as the direct comparison basis.

Until the corpus has declared-dividend or DPS rows, SCS dividend-yield differences are
operator-only diagnostics. Do not publish `dividend_yield_reference_check` rows into
`data_quality_flags`; doing so over-flags users because cash-flow dividends paid are a
lagged proxy, not the same basis as SCS declared yield.
The QC compares available dividend-yield candidates in the operator report and labels the
basis for each candidate. Review
`artifacts/16_scs_ratio_qc/scs_ratio_qc.md` for the ticker, metric, basis, and outlier
reason before changing extraction, statement selection, or market data.

## Cross-Stage State Gate

Use the state gate before and after any D1-affecting publish. It recomputes per-ticker state
from local artifacts plus production D1 and emits the next rerun command for each blocking
reason. This is deterministic and does not call a model.

```bash
python3 run.py --chain state
cat artifacts/state/pipeline_gate.md
```

For CI-style gating:

```bash
python3 pipeline/19_pipeline_state.py --fail-on-blocking
```

Primary artifacts:

- `artifacts/state/pipeline_state.json` — full per-ticker ledger
- `artifacts/state/pipeline_gate.md` — operator summary
- `artifacts/quality/data_quality_flags.jsonl` — serve-consumable query-scoped warning/blocker flags
- `artifacts/quality/data_quality_flags.md` — operator view of open data quality flags
- `artifacts/reports/19_pipeline_state.json` — machine-readable summary
- `artifacts/16_scs_ratio_qc/data_quality_flags.jsonl` — optional Step 16 SCS P/E and dividend-yield caution rows merged into the production quality upload

Treat stage reports as execution logs, not proof of production health. The state gate is the
source of truth for seam drift such as statements without documents, document rows without FTS,
missing source assets, open extraction QC failures, missing ratios, missing PSX market data,
or stale PSX latest rows.
Its warning headline must include all open Step 19 serve-consumable quality flags, including
ratio `unit_or_scale_suspect` and statement `source_report_inconsistent_subtotal` warnings,
not only state-only checks such as stale PSX market prices.
Use `data_quality_flags` for user-facing warning behavior: match on the narrowest available
scope (`ticker`, `affected_table`, `statement_type`, `filing_period`/`period_end`,
`period_duration`, `section`, and `metric`) rather than warning on the whole ticker.
Ratio `unit_or_scale_suspect` flags must include deterministic `user_caution_json` with the
ratio formula, numerator, denominator, period, section, failed stage, and rule. Serve uses this
payload to show a compact calculation note only when the flagged ratio is retrieved. A high
`current_ratio` is not automatically a user-visible unit/scale failure when the underlying
current-asset and current-liability statement rows are clean; keep that as a Step 15 operator
outlier unless a narrower Step 09 source/subtotal issue or statement QC flag exists.

When deterministic QC shows that visible child rows do not add back to a subtotal printed in
the PDF, stay faithful to the PDF-reported subtotal. If visual inspection confirms the source
filing appears internally inconsistent or has an omitted/unlabelled line, classify the issue as
`source_report_inconsistent_subtotal` with warning severity and a `user_caution_json` payload.
Do not fabricate balancing rows. Use blocker severity only when extraction/routing is still
wrong or the source page is too messy to trust.

After every Step 19 refresh that changes flags, publish the ledger to production D1:

```bash
python3 pipeline/13_d1_push.py --quality-flags
```

If Step 16 SCS ratio QC was refreshed, run it before the D1 quality-flags publish. Its
`user_caution_json` payload is deterministic and is used by serve to append formula/numerator/
denominator caution text below answers only when the flagged row is retrieved.

Then check the serve admin health matrix (`/api/admin/data-quality/matrix?periods=12`
or `/admin/traces`) before calling the publish complete.

Use `--only` to rerun a single stage from a chain after a targeted fix:

```bash
python3 run.py --chain pl --ticker HBL --only normalize_pl
python3 run.py --chain pl --ticker HBL --only derive_pl
python3 run.py --chain pl --ticker HBL --only flatten_pl
```

## LTM Debugging

LTM is derived in `pipeline/11_derive.py` as:

```text
current YTD + prior 12M - prior same-duration YTD
```

The LTM stage requires direct provenance for all three components: `source_file`, `source_pages`, and `source_url`.

When LTM is missing, check `artifacts/11_derive/ltm_pl_qc.json` and `artifacts/11_derive/ltm_cf_qc.json`.

Expected explanations include:

- `missing_prior_annual`: no annual 12M period before the current YTD period.
- `missing_prior_same_duration`: no same-duration YTD period exactly one year earlier.
- `missing_direct_provenance_component`: normalize did not attach source file/pages/url to one or more components.
- `no_common_numeric_fields`: the three components do not share usable numeric fields.

If many tickers show `missing_direct_provenance_component`, inspect route/normalize before touching LTM math.

## Flatten Unit QC

Step 12 is the final deterministic unit gate before D1. When cumulative PL/CF rows carry
ambiguous source units, inspect `data/json_{pl,cf}/{TICKER}.json` and `data/flat/{pl,cf}.jsonl`
side by side. A source `raw_unit_type` such as `thousands | rupees` should remain QC-flagged
unless the value can be resolved deterministically. A `millions` label may be corrected to
`thousands` only when the same ticker, period, duration, and canonical field in the peer section
uses the same raw scale. Peer-confirmed corrections should fix the flattened value without
adding a user-facing `qc_flag`; unresolved ambiguity should stay in `qc_flag` so ratios exclude
the row.

If extraction emits both `thousands` and `millions` in one raw unit label, canonicalize the
period to thousands while preserving the ambiguity warning. Do not scale large P&L fields and
small P&L fields differently inside the same period. The repair loop is:
`python3 pipeline/10_normalize.py --type pl --ticker TICKER`,
`python3 pipeline/11_derive.py --type pl --ticker TICKER`,
`python3 pipeline/12_flatten.py --type pl`, then publish PL, rebuild ratios/state, and push
quality flags if the values are correct.

## Ratio Debugging

Ratios are rebuilt in `pipeline/15_ratios.py` from D1 `financial_statements`.
Step 15 must compute ratios only from clean canonical period groups. If normalize or flatten
marks any sibling row for the same ticker, period, duration, section, statement type, and
canonical name with `scale_discontinuity`, `ambiguous_unit_type`, or another QC flag, every
method for that canonical period is excluded from ratio inputs. The source rows can remain in
`financial_statements` for provenance, but they must not be used as a ratio numerator or
denominator. Rebuild ratios after fixing the underlying statement row.

If an LTM row follows a known `scale_discontinuity` and remains in the same scale band, Step 12
should carry the warning forward as `scale_discontinuity_carryforward`. That is a ratio-input
guard for inherited bad scale, not a ticker-specific suppression rule.

For ratios with multiple profit-and-loss components (`net_margin`, `gross_margin`,
`cost_to_income`, and insurance ratios), Step 15 must select a complete component set from the
same extraction/derivation `method`. Do not let one component come from `LTM_9M...` and another
from `LTM_3M...`; that creates mathematically valid SQL output from incoherent source periods.
If a higher-priority method is QC-flagged for one component, the ratio should fall back to the
best complete same-method set or not emit.

After every ratio rebuild, inspect:

```bash
cat artifacts/15_ratios/coverage_report.json
cat artifacts/reports/15_ratios.json
```

Coverage explanations must separate:

- no LTM statement rows,
- no LTM PL rows,
- missing `net_profit`,
- missing `revenue_net` or `total_income`,
- missing PIT balance sheet rows at the same `period_end`,
- missing `total_equity` or `total_assets`,
- zero denominators,
- net-loss tickers that still correctly produce negative ratios.

Net losses are not a reason for missing ROE, ROA, or net margin by themselves. They should produce negative ratio values unless a required denominator/input is missing or zero.

## D1 Push and Serving Safety

`pipeline/13_d1_push.py` is the boundary that can affect live serving. Before changing it, remember the observed failure mode in `psxgpt-serve`: `D1_ERROR: Currently processing a long-running import` caused live query failures during a multi-minute import lock.

Required direction for D1 publishing fixes:

- Prefer shadow-table or staging-table writes plus an atomic swap over long in-place replacement.
- Verify row counts before swap.
- Keep generated SQL under `artifacts/13_d1_push/` and the report at `artifacts/reports/13_d1_push.json`.
- During rehearsal, run a read loop or serving health query while pushing and assert no `D1_ERROR: Currently processing a long-running import` occurs.
- After a real push, check `psxgpt-serve`:
  `ADMIN_TOKEN="$(cat .admin-token.local)" curl -fsS -H "Authorization: Bearer $ADMIN_TOKEN" "https://psxgpt-serve-test.isfandiyar-shaheen.workers.dev/api/admin/issues?hours=1&limit=100"`
- If Step 19 quality flags changed, also run `python3 pipeline/13_d1_push.py --quality-flags` and confirm the admin data-quality matrix returns the expected green/yellow/red state.

Do not call a D1 publish fix complete until serve-side `/api/admin/issues` shows no new `d1_lock` failures and the affected serving query replays correctly.

## Operator Handoff

When reporting pipeline work, include:

- the earliest root cause stage,
- files changed,
- exact commands rerun,
- D1 tables refreshed,
- before/after coverage counts,
- residual missing tickers grouped by reason,
- eval or serving query used to verify downstream behavior.
