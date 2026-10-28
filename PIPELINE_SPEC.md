---
title: "psxGPT Pipeline: Architecture"
description: "The converged pipeline: three model roles for the financial-statement spine, one merged review pass that names statements at first pass, bounded QC loops, and automated costed repair triage with a re-run / accept / quarantine decision."
---

# psxGPT Pipeline: Architecture

This is the canonical pipeline spec. The pipeline's job is to build evidence contracts for
`psxgpt-serve`: source-linked rows, searchable retrieval corpora, source assets, ratios,
quality flags, and a state ledger that explains whether the corpus is complete enough to
serve.

The pipeline is organized around one operating rule:

**Every downstream answer should be traceable to a source artifact, a deterministic
derivation, or a scoped quality warning.**

That means each stage must answer three questions:

- What did it consume?
- What did it publish for the next stage?
- What should an operator inspect when the output is wrong?

The financial-statement spine has three model roles:

1. **Parse**: Gemini Flash Lite converts page PDFs to markdown.
2. **Review**: DeepSeek V4 Pro reads each page once and returns statement tags, basis,
   extraction readiness, and BM25 search text.
3. **Extract**: DeepSeek V4 Pro turns routed statement pages into Ref-column markdown rows,
   with a scoped Gemini-from-PDF repair mode when QC points to bad markdown.

The remaining statement-spine stages are deterministic code and gates: page gating, routing,
formula QC, normalization, derivation, flattening, publishing, ratios, market/reference
checks, and state reconciliation. Auxiliary retrieval corpora such as PSX announcements and
macro reports may reuse parse-style model calls, but they publish to separate tables and do
not add new statement-extraction roles.

The main levers are:

- **source provenance**: every published row must point back to source pages or attachments;
- **canonical metrics**: extracted labels become stable fields serve can query;
- **basis and section**: consolidated/unconsolidated context and ratio denominator basis stay
  explicit;
- **stage artifacts**: manifests and reports are the debugging surface, not console counts;
- **scoped quality flags**: usable-but-caveated data carries deterministic warning payloads;
- **state ledger**: Step 19 reconciles completeness across statements, documents, ratios,
  market data, assets, and flags;
- **normal rerun path**: fix the earliest bad artifact, then rerun downstream stages in order.

## Reading path

Use this spec as an operator map, not a script catalog:

1. **Motivation**: the opening rule explains why source-linked, deterministic, caveated evidence
   is the product boundary.
2. **Concepts**: evidence contracts, provenance, canonical metrics, basis/section, artifacts,
   quality flags, state ledger, and normal rerun path define the vocabulary.
3. **The Shape** gives the whole flow and the artifact each stage publishes.
4. **Core Principles** explains the invariants that keep the corpus auditable.
5. The numbered **Step** sections define what each stage consumes, publishes, and how to inspect
   failure.
6. **The Two Control Loops** explains the repair loops that keep model work bounded.
7. **run.py Chains** shows how the individual stages are composed for real runs.
8. **Model, Cost, and Caching Policy** explains why model calls are constrained and recorded.
9. **Durable Choices** and **Changeable Choices** separate architecture commitments from tunable
   implementation details.

When debugging, do not start at the last failed command. Start at the earliest artifact that could
explain the symptom, then rerun downstream stages in order.

## Progress Documentation Discipline

Pipeline progress should be documented through emitted datapoints, not retrospective prose alone.
For every stage that materially changes the corpus, preserve or update:

- **input scope**: ticker, filing, period, source document, command, and relevant flags;
- **published output**: artifact path, D1 table, R2 namespace, report path, or state-ledger field;
- **accept/reject evidence**: skipped pages, selected pages, extracted rows, QC failures,
  quarantined items, or caveated slices;
- **quality result**: formula checks, semantic checks, market/reference checks, freshness checks,
  asset completeness, or Step 19 status;
- **repair pointer**: earliest implicated artifact, recommended rerun command, expected downstream
  stages to refresh;
- **user-visible impact**: which serve surface, query class, source panel, ratio, warning, or eval
  result changed.

This is the discipline that turns the pipeline from a collection of scripts into an auditable
corpus builder. A later reader should be able to tell what progressed, what remains uncertain, and
which datapoint proves it.

---

## The Shape

| Step | Script | Posture | Output |
|---|---|---|---|
| 01 | `01_download.py` | network | `database_pdfs/` |
| 01b | `01_ingest.py` | deterministic | coverage report, gap recommendations |
| 02 | `02_parse.py` | model (Gemini Flash Lite) | `markdown_pages/`, parse usage log |
| 03 | `03_gate.py` | deterministic | skip manifest, tag manifest |
| 04 | `04_tag.py` | model (DeepSeek V4 Pro, 1 call/page) | `review/<ticker>/<year>/` per-filing JSONL |
| 05 | `05_fix.py` | model (loop: re-parse ReOCR pages) | updated `markdown_pages/`, updated `review/` |
| 06 | `06_route.py` | deterministic | extraction manifest |
| 08 | `08_extract.py` | model (DeepSeek V4 Pro from markdown; scoped Gemini-from-PDF repair mode) | `data/extracted_{type}/` Ref-column markdown |
| 09 | `09_qc.py` | deterministic | `artifacts/qc_failures.jsonl` |
| 10 | `10_normalize.py` | deterministic | `data/json_{type}/{TICKER}.json` |
| 11 | `11_derive.py` | deterministic | quarterly + LTM rows appended to JSON |
| 12 | `12_flatten.py` | deterministic | `data/flat/{type}.jsonl` |
| 13 | `13_publish.py` | deterministic | local SQLite test DB |
| 13b | `13_d1_push.py` | deterministic | D1 remote (single-file wrangler upload) |
| 14 | `14_triage.py` | deterministic | `artifacts/triage/triage.md`, `recommended_actions.json` |
| 15 | `15_ratios.py` | deterministic | D1 `ratios` table |
| 16 | `16_market_data.py` + `16_market_qc.py` + `16_scs_ratio_qc.py` | deterministic + external reference | `artifacts/16_market_data/market_data_report.json`, `artifacts/16_market_qc/market_qc.json`, `artifacts/16_scs_ratio_qc/scs_ratio_qc.json` |
| 17 | `17_publish_documents.py` | deterministic | D1 `financial_documents_new` + `filings_fts_new` (`--gap` for all-missing) |
| 18 | `18_publish_assets.py` | deterministic | `pdf_thumbnails/` JPGs (360px/q70/110dpi) + R2 page PDFs/thumbnails (S3 bulk) |
| 19 | `19_pipeline_state.py` | deterministic | `artifacts/state/pipeline_state.json`, `artifacts/state/pipeline_gate.md` |
| 20 | `20_publish_announcements.py` | network + model parse + deterministic publish | D1 `psx_announcements` + `psx_announcements_fts`, `announcement_documents/`, `announcement_markdown/` |
| — | `02_parse.py --reparse-broken` | model (Gemini) | re-parse garbled `markdown_pages/` |

Rows 02, 04, and 08 are the three model roles in the financial-statement spine. Step 05 is a
loop that re-enters 02 and then 04 for pages flagged `ReOCR`, and Step 08 can be rerun in a
Gemini-from-PDF repair mode when QC points to bad markdown as the source. The mental model is
four verbs: **parse → review → extract → publish**, with a deterministic QC gate between each.

Step 07 was removed; routing is now handled entirely within 06_route.py.

---

## Core Principles

**Key Point: Source First**

Every published row traces to a filing page. A row that cannot name its source page is not
published. (Unchanged from the destination spec — this is the foundation.)

**Key Point: Three Statement-Spine Model Roles**

In the financial-statement spine, parsing, reviewing, and extracting are the only model
roles. Routing, QC, derivation, flattening, and publishing are deterministic. If a step can
be deterministic, it is — a wrong value with no check that can fail looks identical to a
right one.

This count covers the **financial-statement spine** (steps 01–16). The auxiliary **macro
ingestion track** (see *Macro Documents*, below) reuses the same Gemini parse model to build
a separate retrieval-only surface; it adds no new model role, only another use of parse.
The **PSX announcements track** follows the same separation: it downloads attachments,
parses them to markdown with Gemini, then publishes a retrieval-only announcements corpus.

**Key Point: Repair Outputs Must Be Actionable**

Long-running repair goals must keep a dated run-state file copied from
`docs/goal_run/RUN_STATE_TEMPLATE.md`. Repair-driving issue artifacts should follow
`docs/goal_run/ISSUE_OUTPUT_CONTRACT.md`: include one primary category, the earliest
implicated stage, ticker/period/source-page evidence, recommended rerun command, and
before/after production impact. A ticker list without category, stage, and rerun command is
not enough to drive a reliable repair loop.

**Key Point: One Review Pass — Structure and Summary Together**

The page is read once. A single Pro call returns tags, basis, extraction score, and search
text. The page body is the cost driver and is paid once, not twice. The outputs stay
separate artifacts downstream; only the *call* is merged.

**Key Point: Tags Route, and Only Routing Tags Exist**

A tag exists only if a downstream extractor or selector consumes it. Statement pages are
named at first pass (`profit_loss`, `balance_sheet`, `cash_flow`, `changes_in_equity`,
`comprehensive_income`), each with consolidation `basis`. Narrative tags with no extractor
(`md&a`, `useful_note`) are not emitted — those pages remain fully retrievable via
`search_text`.

**Key Point: Ground Truth Is the Extractions We Already Trust**

New prompts and models are validated against the pages our existing, trusted extractions came
from (`step2_statement_pages.json`), not against other prompts. The merged review pass was
accepted because it reproduced that selection 6/6 (consolidated + unconsolidated PL/BS/CF,
correct basis) on a full annual report in one pass.

**Key Point: Every Gate Is a Bounded Loop**

A QC gate does not just pass or fail. On failure it re-enters the cheapest layer that could
be the cause, narrows scope to the failing items, retries up to a cap, and quarantines what
survives the cap. This single rule generates the control flow.

**Key Point: Cost Is Computed, Not Guessed**

Token usage and price come from provider usage fields and a local price table. Because every
artifact carries provenance and every model has a known price, the cost to fix any error is
computable before it is spent.

---

## Step 01: Download

Purpose: fetch PDFs from the PSX financials portal.

Script: `01_download.py` — Playwright-driven browser automation.

Outputs: `database_pdfs/{ticker}/{year}/{filing}.pdf`

---

## Step 01b: Ingest / Coverage Analysis

Purpose: report download coverage against the expected-annual baseline, flag gaps, and emit
a recommendation on what to download next.

Script: `01_ingest.py` — deterministic, uses an LLM synthesis call for the recommendation
text only.

Outputs: `artifacts/reports/01_ingest.json`, `artifacts/01_ingest/` report files.

---

## Step 02: Parse

Purpose: turn each page PDF into markdown in one model call.

Model: **Gemini Flash Lite** (`gemini-3.1-flash-lite`, configurable via
`GEMINI_PARSE_MODEL`). Reads the page PDF directly. There is no separate image conversion
step for parsing.

Inputs:

- `database_pdfs/{ticker}/{year}/{filing}/page_{NNN}.pdf`

Outputs:

- `markdown_pages/{ticker}/{year}/{filing}/page_{NNN}.md`
- `artifacts/reports/02_parse.json` (parsed/failed counts, model name)

Page identity is stable across every later stage:

```text
ticker / filing_year / filing_name / page_number
```

Fix and triage:

- A page the review pass cannot make sense of is re-parsed here (the parse↔review loop via
  step 05).
- "Repair" is not a stage. A bad parse is a re-parse of that one page, not a ladder.

---

## Step 03: Free Page Gate

Purpose: eliminate certain-useless or corrupt pages before any Pro spend.

Script: `03_gate.py` — deterministic, conservative, first-match-wins. A false keep is cheap
(a few review tokens); a false skip is expensive (it can remove the only useful page).

Checks: blank / near-empty, thin after stripping headings+images, Urdu-heavy (current
scope), repeated-line corruption, broken table structure, image-only with no useful cue.

Outputs:

- `artifacts/03_gate/skip_manifest.json` — pages that never reach the review pass
- `artifacts/03_gate/tag_manifest.json` — surviving pages queued for step 04
- `artifacts/reports/03_gate.json`

---

## Step 04: Review Pass

Purpose: read each surviving page once and return everything the rest of the pipeline needs
to route and retrieve it.

Model: **DeepSeek V4 Pro** (`deepseek-v4-pro`, configurable via `DEEPSEEK_REVIEW_MODEL`,
base URL `DEEPSEEK_API_BASE`), one call per page, JSON mode. Prompt pack:
`context/04_tag/` (`rules.md` + `format.md`). The instruction block is a stable prefix; the
page body follows it, so DeepSeek's automatic prefix caching makes the instructions
near-free after warm-up. Nothing variable (ticker, page number, date) is ever interpolated
into the prefix.

Input: `artifacts/03_gate/tag_manifest.json` → `markdown_pages/` pages

Output: `review/{ticker}/{year}/{filing}.jsonl`, one row per page:

```json
{
  "relative_path": "LUCK/2024/LUCK_Annual_2024/page_290.md",
  "section_tags": [
    {"tag": "cash_flow", "confidence": 1.0, "basis": "consolidated"}
  ],
  "extraction_score": "OK",
  "search_text": "consolidated cash flow, operating 41.2bn, investing -18.7bn, financing -9.3bn, net increase in cash"
}
```

Routing tags (only tags a downstream consumer uses):

- statements (carry `basis` = consolidated | unconsolidated | unknown):
  `profit_loss`, `balance_sheet`, `cash_flow`, `changes_in_equity`, `comprehensive_income`
- `statement_note`, `multi_year`, `ceo_comp` (no basis)

`extraction_score` ∈ {`OK`, `Fix`, `ReOCR`} drives the parse↔review loop. `search_text` is
a compact 18–40 word BM25 field, empty for effectively-empty pages.

---

## Step 05: Fix (Parse↔Review Loop)

Purpose: re-parse pages that step 04 flagged `ReOCR`, then re-tag them.

Script: `05_fix.py` — orchestrates re-entry into steps 02 and 04 for flagged pages.

Reads `review/**/*.jsonl` to find pages with `extraction_score = ReOCR`, re-runs
`02_parse.py` for those specific pages, then re-runs `04_tag.py` to update the review
output. Capped at N attempts; surviving failures are quarantined.

---

## Step 06: Extraction Routing

Purpose: turn review tags into focused extraction jobs. Deterministic — a query over
`review/` JSONL files, not a model call.

Script: `06_route.py`

Selection rule: for each statement type and basis, take the highest-confidence page(s) above
threshold. The statement map is built directly from `(tag, basis, confidence)`.

Outputs:

- `artifacts/06_route/manifest.json` — primary extraction job list
- `artifacts/06_route/statement_pages.json` — legacy format consumed by downstream steps
- `artifacts/reports/06_route.json`

Note: step 07 (locator) was removed. Multi-page tie-breaking for the same `(tag, basis)` is
handled inline within `06_route.py`.

---

## Step 08: Structured Extraction

Purpose: turn selected pages into structured statement and operating data.

Script: `08_extract.py`

Model: **DeepSeek V4 Pro** (`deepseek-v4-pro`, configurable via `DEEPSEEK_EXTRACT_MODEL`),
registry-driven. One parameterized extractor over a **statement registry** (`registry.py`),
where name, prompt pack, and QC rules are data, not duplicated code. Each statement type
uses separated packs: `context/08_{type}/fields.md`, `format.md`, `rules.md`.

Input: `artifacts/06_route/manifest.json` → `markdown_pages/` pages

Outputs: `data/extracted_{type}/{TICKER}_{period_type}_{date}_{section}.md` — Ref-column
markdown tables. `artifacts/reports/08_extract.json`, extraction usage log.

Rows preserve: source label, canonical label, period, value, unit, statement type,
consolidation level, source page. The extractor emits **reported values only** — it never
derives Q4, LTM, ratios, or market data.

---

## Step 09: Extraction QC

Purpose: prove rows are source-grounded and coherent. Deterministic. Cheapest checks first.

Script: `09_qc.py`

Checks currently implemented:

1. **Schema** — every canonical label is a valid field for this statement type.
2. **Formula** — every Ref formula row (e.g. `H=A+B+C`) reconciles from its referenced rows.

Checks planned but not yet implemented:

- Source match — value appears in the cited markdown page.
- Units/periods — canonical labels valid, units consistent, periods match filing.
- Collision — duplicate canonical rows, parent/NCI/consolidated labels preserved.

Outputs: `artifacts/qc_failures.jsonl` (appended globally, not per-ticker),
`artifacts/reports/09_qc.json`.

When the filing's printed subtotal does not equal the visible child rows, the pipeline stays
true to the PDF: it publishes the reported subtotal and records a scoped warning with
`category=source_report_inconsistent_subtotal`. These are not repaired by inventing balancing
rows. They remain visible in data-health and user caution surfaces so the linked filing page
can be inspected.

Known-acceptable deviations are encoded in per-type allowlists under
`artifacts/allowlists/` (`pl.json`, `cf.json`, `bs.json`, plus `bs_exclusions.json`). These
are consumed by the deterministic normalize/derive/flatten steps (10–12), which suppress a
listed deviation rather than re-running it.

---

## Step 10: Normalize

Purpose: convert extracted Ref-column markdown into per-ticker structured JSON keyed by
canonical name. The format that steps 11 (derive) and 12 (flatten) consume.

Script: `10_normalize.py` — deterministic.

Inputs: `data/extracted_{type}/*.md`

Outputs: `data/json_{type}/{TICKER}.json`

PL and CF parse period-column headers (`12M Dec 2023`); BS parses date-column headers
(`30 Jun 2024`). Ceiling-date guard: rejects BS comparative-column dates that exceed the
filing date (catches OCR year-off errors). `select_best_source_flow()` picks the primary
annual filing over quarterly comparatives when both cover the same period.

When extraction emits a mixed scale label containing both `thousands` and `millions`,
Step 10 canonicalizes the period to `thousands` while keeping the raw unit label for QC
visibility. That prevents downstream stages from multiplying smaller P&L lines by 1,000
while leaving larger lines in the correct scale.

---

## Step 11: Derive

Purpose: derive non-reported periods deterministically after reported rows pass QC. The
model never does this arithmetic.

Script: `11_derive.py` — deterministic.

```text
Q1 = reported Q1
Q2 = reported H1 − Q1
Q3 = reported 9M − H1
Q4 = annual − 9M
LTM = latest YTD + prior annual − prior comparable YTD
```

Balance sheet is point-in-time and is not quarter-derived. Outputs include derived rows plus
arithmetic QC (`Q1+Q2=H1`, `9M+Q4=Annual`, LTM identity). A derived-row failure points at
its reported inputs first, runtime derivation code last.

LTM derivation may produce multiple LTM rows for the same period_end when both a 3M-base
and a 9M-base quarterly are available for a ticker. Step 15 (ratios) deduplicates these,
preferring the 9M-based LTM.

---

## Step 12: Flatten

Purpose: merge per-ticker JSON + derived periods into a single flat JSONL per statement
type. This is the canonical format read by D1 upload and by the market QC step.

Script: `12_flatten.py` — deterministic.

Inputs: `data/json_{type}/{TICKER}.json` (normalized + derived)

Outputs: `data/flat/{type}.jsonl` (pl.jsonl, bs.jsonl, cf.jsonl)

PL/CF: emits 3M, YTD, 12M, and LTM rows. BS: point-in-time only, with dedup (prefer
primary/annual over quarterly comparative). Attaches source URL
(`https://source.psxgpt.com/PDF_PAGES/...`).

Step 12 is also the last deterministic unit/scale gate before D1. For cumulative PL/CF rows,
it must honor ambiguous source units such as `thousands | rupees`, and it may correct a
`millions` label to `thousands` when the same ticker/period/duration peer section reports the
same raw scale. If the raw label contains both `thousands` and `millions`, Step 12 keeps the
canonical value scale at thousands and preserves the ambiguity in `qc_flag`. Peer-confirmed
unit corrections are value fixes, not user-facing warnings. Ambiguous units that cannot be
resolved remain in `qc_flag`, so Step 15 will not use them as ratio inputs.

---

## Step 13: Publish (Local Test)

Purpose: load flattened rows into a local SQLite database for validation before D1 upload.

Script: `13_publish.py` — deterministic.

D1 is SQLite-compatible, so the same schema and loader code works for both the local test
target and the remote D1 instance. The local SQLite file can be queried with standard
`sqlite3` tooling.

Inputs: `data/flat/{type}.jsonl`

Outputs: local SQLite DB file, `artifacts/reports/13_publish.json`

---

## Step 13b: D1 Push

Purpose: upload the validated flat rows to Cloudflare D1 (`psx` database).

Script: `13_d1_push.py` — generates a single SQL file per type and uploads it with one
`npx wrangler d1 execute --file` call. Wrangler handles chunking internally.

**Important — D1 locks during import.** Each upload locks the database for its full
duration (~2–5 min per type for a full rebuild). The live app will return
`D1_ERROR: Currently processing a long-running import` for any query during this window.
Run pushes one type at a time (not concurrently) and schedule during off-hours if uptime
matters.

**Do not re-introduce batching.** A previous implementation split each SQL file into
hundreds of small wrangler calls, locking D1 repeatedly and causing concurrent-write
failures. The single-file approach is correct.

This step is run separately from `run.py` chains (the `full` chain ends at 13_publish).
Typical invocation:

```bash
python pipeline/13_d1_push.py --type pl
python pipeline/13_d1_push.py --type cf
python pipeline/13_d1_push.py --type bs
```

Outputs: rows in D1 `financial_statements` table, SQL file in `artifacts/13_d1_push/`.

---

## Step 14: Triage

Purpose: turn QC failures into priced, clustered repair decisions.

Script: `14_triage.py` — deterministic. Reads `artifacts/qc_failures.jsonl`.

**1. Diagnoser.** Deterministic rules map `(gate, signal)` → layer + re-entry point +
confidence:

- formula fails AND source clean → `extraction` → re-enter step 08
- same canonical field fails across many tickers → `systematic` → fix pack, do not re-run
- review `score = ReOCR` → `parse` → re-enter step 02

**2. Cost estimator.** Re-entry point × scope × local price table (including cascade cost):

```text
PARSE   = pages × Flash Lite (in+out)
REVIEW  = pages × Pro (cached prefix + body, out)
EXTRACT = statement pages × Pro (in+out)
```

**3. The three-way decision.** Each failure cluster surfaces with cause, layer, confidence,
scope, estimated cost, and exactly three actions:

- **Re-run** — fix via the diagnosed loop, scoped.
- **Accept** — known-OK deviation; append to the relevant allowlist.
- **Quarantine** — cannot fix now; block publish for that scope, surface later.

**4. Auto vs ask policy:**

```text
auto-run   if  layer ∈ {parse, extraction} AND confidence = high AND cost < $0.10 AND attempts < cap
ask        otherwise  (expensive, low-confidence, or systematic)
quarantine if  attempts hit the cap without passing
```

Outputs: `artifacts/triage/triage.md`, `artifacts/triage/recommended_actions.json`.

---

## Step 15: Ratios

Purpose: rebuild the `ratios` table on D1 from the `financial_statements` table.

Script: `15_ratios.py` — deterministic, runs after D1 upload is complete.

### Universal ratios (all sectors)

| ratio_name | Formula | Notes |
|---|---|---|
| `roe` | `net_profit / total_equity` | Latest-prior-or-same-date BS |
| `roa` | `net_profit / total_assets` | Latest-prior-or-same-date BS |
| `net_margin` | `net_profit / income` | Income field priority: `revenue_net` → `total_income` → `net_premium` → `operating_revenue` |
| `gross_margin` | `gross_profit / revenue_net` | Corporate only; absent for banks/insurance |
| `current_ratio` | `total_current_assets / total_current_liabilities` | Corporate only; absent for banks/insurance |
| `debt_to_equity` | `total_liabilities / total_equity` | All sectors; interpret differently for banks (high leverage is normal) |
| `asset_turnover` | `income / total_assets` | Income field priority: `revenue_net` → `total_income` → `net_premium` → `operating_revenue` |

### Banking ratios (tickers with `total_income` but no `revenue_net`)

| ratio_name | Formula | Notes |
|---|---|---|
| `cost_to_income` | `\|operating_expenses\| / total_income` | `operating_expenses` stored as negative; ratio flipped positive |
| `net_interest_margin` | `net_interest_income / total_assets` | Uses total_assets as proxy for earning assets; latest-prior-or-same-date BS |

### Insurance ratios (tickers with `net_premium`)

| ratio_name | Formula | Notes |
|---|---|---|
| `loss_ratio` | `\|net_claims\| / net_premium` | `net_claims` stored as negative |
| `expense_ratio` | `\|operating_expenses\| / net_premium` | `operating_expenses` stored as negative |
| `combined_ratio` | `(\|net_claims\| + \|operating_expenses\|) / net_premium` | Requires both fields present |
| `underwriting_margin` | `underwriting_profit / net_premium` | Pre-computed by extractor |
| `investment_yield` | `investment_income / net_premium` | Investment income as fraction of written premium |

### Income field fallback chain

`net_margin` and `asset_turnover` are computed via four successive INSERT statements, each
guarded so later legs fire only when all prior legs are absent for that exact
`(ticker, period_end, period_duration, section)` tuple:

1. `revenue_net` — corporates
2. `total_income` — banks
3. `net_premium` — insurance companies
4. `operating_revenue` — holding companies (e.g. IGIHL, FCSC)

### basis column

Every row in the `ratios` table carries a `basis TEXT` column that names the canonical field
used as the denominator (or primary driver) in the calculation. This is the authoritative
signal for how to label a ratio when displaying it to users:

| ratio_name | basis values | Meaning |
|---|---|---|
| `net_margin` | `revenue_net` / `total_income` / `net_premium` / `operating_revenue` | Which income field was the denominator |
| `asset_turnover` | same as net_margin | Which revenue field was in the numerator |
| `roe` | `total_equity` | Always equity |
| `roa` / `net_interest_margin` | `total_assets` | Always assets |
| `gross_margin` | `revenue_net` | Always |
| `debt_to_equity` | `total_equity` | Always |
| `current_ratio` | `total_current_liabilities` | Always |
| `cost_to_income` | `total_income` | Always |
| insurance ratios | `net_premium` | Always |

Clean high `current_ratio` values are treated as Step 15 operator outliers, not automatic
user-visible unit/scale warnings. User-visible current-ratio cautions should come from a
narrower upstream issue, such as Step 09 source subtotal inconsistency or a statement row
QC flag on the current-asset/current-liability inputs.

### period_duration and P&L-only ratios

P&L-only ratios (net_margin, gross_margin, cost_to_income, all insurance ratios) are computed
via `best_flow_all` which spans `3M / 6M / 9M / 12M / LTM`. This means companies that only
have quarterly or 9M consolidated filings (e.g. June-FY companies that file the annual in
unconsolidated form only) will still have net_margin at 9M even without a full LTM.

When a P&L-only ratio needs multiple fields, Step 15 must choose a complete same-method
component set before computing the ratio. For example, `gross_margin` cannot combine
`gross_profit` from `LTM_9M+prior12M-prior9M` with `revenue_net` from
`LTM_3M+prior12M-prior3M`, even if both rows are individually unflagged. If the highest-priority
method is unavailable because one component was QC-filtered, the ratio falls back to the best
complete same-method set; otherwise it is not emitted.

QC filtering is applied at the canonical period group level. If any row for the same ticker,
period, duration, section, statement type, and canonical name has a non-empty `qc_flag`, all
sibling methods for that canonical period are excluded. This prevents a derived LTM method from
being selected when another method for the same canonical period already exposed a scale or unit
break.

LTM scale discontinuities also carry forward within the same ticker/statement/section/metric
series while the next LTM value remains in the same scale band. That prevents ratios from using
derived LTM rows that inherit a known bad scale without creating a second 100x jump.

Ratios that join to the balance sheet (ROE, ROA, asset_turnover, net_interest_margin) use
`best_flow` (`12M / LTM` only) to ensure the income period is annualised before dividing by
a point-in-time balance sheet figure.

### CTEs

Each INSERT uses its own `WITH` CTE that deduplicates before computing:

- **`best_flow`**: P&L 12M/LTM rows; prefers 9M-based LTM over 3M-based (more stable).
  Partitions by `(ticker, period_end, period_duration, section, canonical_name)` so annual
  12M rows are never displaced by LTM approximations.
- **`best_flow_all`**: Same dedup logic but includes 3M/6M/9M — used for per-period margin
  ratios.
- **same-method flow candidates**: P&L-only multi-component ratios group by `method` first and
  rank complete groups, so every numerator/denominator pair comes from the same reported or
  derived period construction.
- **`best_bs`**: PIT balance-sheet rows; picks highest `id` (most recent upload) per key.

Outputs: D1 `ratios` table, `artifacts/15_ratios/rebuild_ratios.sql`.

---

## Step 16: Market Data + QC

Purpose: publish self-contained market price/market-cap rows to D1 and verify them
against live PSX inputs.

Scripts:

- `16_market_data.py` — fetches PSX DPS EOD close and PSX company-page shares, computes
  market cap, emits SQL, and optionally pushes D1.
- `16_market_qc.py` — verifies D1 `closing_price`, `total_shares`, and `market_cap`
  against the same live PSX inputs.
- `16_scs_ratio_qc.py` — compares the served production D1 statement/ratio corpus, pulls
  SCSTrade all-market, sector, and ranked valuation rows where available as an external
  P/E and dividend-yield reference subset, computes direct P/E from D1 `market_data`
  plus parsed financial statements, compares dividend-yield candidates from legacy D1
  `market_data.dividend_yield` and `cash_flow.dividends_paid / market_cap`, and flags
  material outliers. This is a reference check only; SCS does not overwrite PSX market
  data or parsed financial-statement values.

For each ticker:

1. Fetch latest PSX DPS EOD row. The payload shape is `[timestamp, close, volume, open]`;
   `closing_price` must use index `1`, not index `3`.
2. Get total shares from the PSX company page.
3. Compute `market_cap = closing_price * total_shares`.
4. Upsert `(ticker, date)` into `market_data`.
5. Clear deprecated derived valuation columns on refreshed rows: `pe_ratio`, `pb_ratio`,
   `dividend_yield`, `enterprise_value`, and P/E basis fields.
6. Run QC to compare D1 against live PSX close/share inputs and flag date, close, share,
   or market-cap deviations.
7. Identify tickers with no usable `closing_price` and `market_cap`. These tickers may
   remain in raw statement/document artifacts for audit or future backfill, but they should
   be removed from the serve ticker reference before deployment so price-dependent user
   queries fail as out-of-coverage instead of planning a market-data lane with no rows.
8. Treat stale PSX latest rows as warning-level market-data issues. If PSX itself only
   returns an old latest row, keep the PDF/statement corpus intact, keep the D1 row for
   audit, but publish a `stale_psx_market_price` quality flag so serve health checks and
   price-dependent answers can disclose the stale pricing scope instead of showing green.
9. Run SCS ratio QC for P/E and dividend yield across the served D1 statement/ratio corpus.
   Market-data-only legacy symbols are excluded by default so emitted quality flags line up
   with the health matrix; use `--include-market-only` only for intentional raw market-data
   audits outside the served corpus.
   Direct P/E is computed as
   `latest market_cap / latest annual_or_LTM net_profit`; the check enumerates
   comparable net-profit candidates by period, statement section, and unit scale so
   outliers point to the exact net-income basis that needs repair. Do not reconstruct
   P/E through historical shares outstanding, and do not choose a net-profit row because
   it is closest to SCS. If both direct P/E and SCS P/E are missing,
   or SCS P/E is `0.0` while D1 computes a positive market-cap/net-income P/E, keep the
   note in the SCS report but do not publish a user-facing SCS P/E flag, because there is no
   comparable external reference to fail. The operator report keeps broad 25% SCS
   differences, but production quality flags publish only missing market cap, missing
   annual/LTM net profit, or severe positive direct-vs-SCS P/E differences of at least 3x.
   If SCS is explained by another clean reported-scale candidate, such as an older annual
   or different section basis within 50% of SCS P/E, keep that as operator-only
   reference-basis drift instead of publishing a user-facing caution.
   Non-positive latest annual/LTM net profit is not itself a user-facing data-quality issue.
   Dividend yield is compared against explicit
   candidates: legacy D1 `market_data.dividend_yield` and
   `cash_flow.dividends_paid / market_cap`. The report labels every candidate because
   neither candidate is a canonical declared-DPS source; legacy market-data DY may be
   stale or derived elsewhere and is operator-report-only. User-facing SCS dividend-yield
   flags are disabled until the corpus has declared-dividend or DPS rows. Keep the
   cash-flow `dividends_paid / market_cap` comparison in the operator report only; do not
   publish `dividend_yield_reference_check` rows into production quality flags from this
   proxy basis.
   stale, and cash-flow dividends are lagged cash movements. Use `--scs-only` only for
   quick spot checks; it is not a complete corpus health check.

Commands:

```bash
python3 run.py --chain market --ticker FECTC --push-d1
python3 run.py --chain market --push-d1
python3 run.py --chain market_qc --ticker FECTC
python3 run.py --chain scs_ratio_qc
```

Outputs: `artifacts/16_market_data/market_data_report.json`,
`artifacts/reports/16_market_data.json`, `artifacts/16_market_qc/market_qc.json`,
`artifacts/reports/16_market_qc.json`,
`artifacts/16_scs_ratio_qc/scs_ratio_qc.json`,
`artifacts/16_scs_ratio_qc/scs_ratio_qc.md`,
`artifacts/reports/16_scs_ratio_qc.json`, console summaries.

---

## Step 17: Publish Documents (filing-page corpus + source assets)

Purpose: publish the per-page filing corpus that the serve runtime retrieves and cites —
the `financial_documents_new` table and its `filings_fts_new` BM25 index — and upload the
matching page PDFs and thumbnails to R2 so source previews/links resolve.

Script: `17_publish_documents.py` — deterministic. No model calls; it assembles rows from
artifacts that steps 02 and 04 already produced.

Inputs (per page, already on disk):
- `review/{ticker}/{year}/{filing}.jsonl` — `section_tags` + `search_text` (from step 04)
- `markdown_pages/{ticker}/{year}/{filing}/page_NNN.md` — full page `text` (from step 02)
- `tickers100.json` — `industry` per ticker

Outputs:
- D1 `financial_documents_new` rows (cols: `ticker, industry, filing_type, filing_period,
  filing_year, section_tags, pg, jpg_path, search_text, text`) + `filings_fts_new` rows
- `artifacts/17_publish_documents/financial_documents_new_{TICKER}_{ts}.sql`

```bash
python3 pipeline/17_publish_documents.py --ticker FECTC            # SQL only (dry run)
python3 pipeline/17_publish_documents.py --ticker FECTC --push-d1  # publish D1 rows
```

The generated SQL is idempotent per ticker: it deletes that ticker's existing
`financial_documents_new` + `filings_fts_new` rows, re-inserts, and rebuilds the FTS rows.
The same D1 import-lock warning from step 13b applies — run per ticker (short locks) rather
than one universe-wide push.

> Step 17 publishes only the D1 rows. The page **PDFs and thumbnails** that back the source
> panel are produced and uploaded by step 18 (`18_publish_assets.py`). `--gap` publishes every
> ticker in `financial_statements` that is missing from `financial_documents_new`, resumably.

> **Critical operational note:** This step is **separate from the statement spine** and is
> easy to forget. `financial_statements` and `ratios` are published by `13_d1_push.py` and
> `15_ratios.py`; the *source corpus* is published only here (+ step 18). After any universe
> expansion, run the `documents` chain for the new tickers, or `financial_documents_new`
> silently lags behind `financial_statements` (numeric answers appear with no source previews,
> and FTS cannot retrieve the new filings). `17 --gap` closes the D1 document gap. For
> assets, run `18_publish_assets` with the same ticker list, or `--all` when relying on the
> local upload manifest; `18_publish_assets --gap` is only useful before the D1 document gap
> is closed because it targets tickers missing from `financial_documents_new`.

---

## Step 18: Publish Assets (thumbnails + R2 upload)

Purpose: produce the page thumbnails and upload page PDFs + thumbnails to R2 so
`source.psxgpt.com` resolves source previews and deep links. Two deterministic phases, merged
from psxgptinfrav3's `Step4_ConvertToJPG` + `Step5_UploadToR2`:

1. **Thumbnails** — `pdf_pages/<rel>.pdf → pdf_thumbnails/<rel>.jpg`, 360px width / JPEG q70 /
   110 DPI / RGB / Lanczos, `pdf2image` + PIL, `ProcessPoolExecutor`. Skips existing unless
   `--force-thumbs`.
2. **Upload** — `pdf_pages → PDF_PAGES/`, `pdf_thumbnails → PDF_THUMBNAILS/` via the boto3
   **S3 client** (path-style, `s3v4`), 150 `ThreadPool` workers, local manifest skips
   already-uploaded keys.

```bash
.venv/bin/python pipeline/18_publish_assets.py --tickers FECTC
.venv/bin/python pipeline/18_publish_assets.py --all                 # whole corpus (manifest skips uploaded keys)
.venv/bin/python pipeline/18_publish_assets.py --tickers FECTC --thumbnails-only
```

Step 18 depends on the repo environment (`pdf2image`, `Pillow`, `boto3`, Poppler). The
`documents` chain uses the repo `.venv` automatically; direct step-18 commands should do the
same.

> **Use the R2 S3 endpoint, not the Cloudflare REST API.** The S3 endpoint
> (`<account>.r2.cloudflarestorage.com`) has no low rate limit and sustains 150-way
> concurrency. The Cloudflare client API (`api.cloudflare.com`) is capped at ~1,200 requests
> per 5 minutes and will both stall a bulk upload and starve concurrent D1 `wrangler` pushes.
> Auth uses `CLOUDFLARE_R2_ACCESS_KEY_ID` / `CLOUDFLARE_R2_SECRET_ACCESS_KEY` (an R2 token's
> S3 credentials — *not* a `cfat_`/`cfut_` API-token value, which only works over REST).

---

## Step 19: Pipeline State Gate

Purpose: recompute production readiness per ticker by joining local artifacts with live D1
state. This catches drift between stages that no single run report can see.

Script: `19_pipeline_state.py` — deterministic. No model calls.

Inputs:

- local parsed/reviewed/flat artifacts (`markdown_pages/`, `review/`, `data/flat/`)
- open extraction QC failures (`artifacts/qc_failures.jsonl`)
- source asset upload manifests (`artifacts/18_publish_assets/manifest.json` or
  `artifacts/r2_upload/manifest.json`)
- market QC report (`artifacts/16_market_qc/market_qc.json` or
  `artifacts/reports/16_market_qc.json`)
- live D1 tables: `financial_statements`, `financial_documents_new`, `filings_fts_new`,
  `ratios`, `market_data`

Outputs:

- `artifacts/state/pipeline_state.json` — one row per ticker with status, blocking reasons,
  warnings, and recommended rerun commands
- `artifacts/state/pipeline_gate.md` — operator summary
- `artifacts/quality/data_quality_flags.jsonl` and
  `artifacts/quality/data_quality_flags.json` — query-scoped open data quality flags for
  serving warnings
- `artifacts/quality/data_quality_flags.md` — compact operator view of those flags
- `artifacts/reports/19_pipeline_state.json` — summary counts

The state summary's warning ticker count and warning reason counts are derived from the same
open Step 19 quality flags that serve can consume. They must include market warnings, statement
source/QC warnings, and ratio unit/scale warnings. If `pipeline_gate.md` says fewer warning
tickers than `artifacts/quality/data_quality_flags.jsonl`, the state gate is stale or broken.

### Data quality flag contract

Step 19 emits one row per open warning/blocker that serve can match against retrieved or
requested data. The flag is scoped as narrowly as the producing stage can support. Statement
QC failures carry `ticker`, `affected_table='financial_statements'`, `statement_type`,
`filing_period`, `period_end`, `section`, `metric`, `source_file`, and `source_pages`. Ratio
outliers carry `affected_table='ratios'`, `metric`, `period_end`, `period_duration`,
`section`, `basis`, `value`, and a deterministic `user_caution_json` payload with the formula,
numerator, denominator, result, failed stage, and rule. Market gaps carry ticker-level
`affected_table='market_data'`.
Stale PSX latest rows carry `category='stale_psx_market_price'`, `affected_table='market_data'`,
`period_end` equal to the stale PSX/D1 market date, and `stale_days` in the flag payload.

Core fields:

| field | meaning |
| --- | --- |
| `flag_id` | stable deterministic identifier |
| `ticker` | affected PSX ticker |
| `severity` | `blocker` or `warning` |
| `status` | currently `open` |
| `affected_table` | table or data surface the warning applies to |
| `statement_type` | PL/BS/CF semantic name when relevant |
| `filing_period`, `period_end`, `period_duration`, `section` | query-scope match keys |
| `metric`, `basis`, `value` | canonical/ratio field details |
| `failed_stage`, `origin_stage`, `rule` | root-cause stage and rule |
| `source_file`, `source_pages` | repair provenance when available |
| `message` | user-facing warning seed |
| `recommended_command` | operator repair command |
| `user_caution_json` | optional deterministic answer caution payload with formula, numerator, denominator, result, and reference check |

Serve should not treat this as a ticker blacklist. It should warn only when the answer uses
or requests data that overlaps the flag scope. Examples:

- `AGP cash flow annual_2025` overlaps a `financial_statements/cash_flow/annual_2025`
  blocker, so show the warning.
- `AGP revenue annual_2025` does not overlap that cash-flow flag, so do not warn.
- If a ticker has no usable close and market cap, remove it from the served ticker reference
  before deploying serve. Then a query such as `AASM market cap` becomes out-of-coverage
  rather than a query-scoped warning, because no priceable served ticker exists.
- A ratio answer should warn when `ticker + ratio_name + period_end + period_duration +
  section` overlaps a `ratios` flag.

Commands:

```bash
python3 run.py --chain state
python3 pipeline/19_pipeline_state.py --fail-on-blocking
```

This stage is the gate before claiming a production upload is healthy. Stage reports remain
execution logs; Step 19 is the cross-stage state ledger.

Step 16 (`16_scs_ratio_qc.py`) also emits `artifacts/16_scs_ratio_qc/data_quality_flags.jsonl`
for SCS P/E and dividend-yield reference cautions. `13_d1_push.py --quality-flags` merges
that file with the Step 19 ledger when present. Those Step 16 rows include
`user_caution_json` so serve can append a deterministic calculation note below an answer
only when the flagged market/statement row is actually retrieved.

---

## Step 20: Publish Announcements (ticker-specific PSX notices)

Purpose: download ticker-specific corporate announcements from the PSX corporate
announcements feed and publish a searchable unstructured corpus. This is separate from
Step 17's filing-page corpus: announcements are single notices, not annual/quarterly report
pages, and they live in `psx_announcements` plus `psx_announcements_fts`.

Script: `20_publish_announcements.py` — deterministic download/publish logic with a Gemini
attachment-to-markdown parse step. It reads PSX's corporate-announcements JSON endpoint by
date, resolves the site's JavaScript file redirect to the stable byte-serving endpoint,
downloads every attachment format PSX exposes, parses each attachment to markdown, builds a
compact BM25 discovery field, and emits scoped SQL for D1. Attachment-to-markdown parsing
uses Gemini 3.1 Flash Lite, matching the financial-page parser. This step does not use
deterministic PDF/OCR fallbacks; if Gemini cannot parse an attachment, the markdown artifact
records the failure and the report counts `gemini_failed`. Gemini failure placeholders are
local audit artifacts and are skipped from generated D1 SQL by default;
`--include-parse-failures` exists only for explicit debugging. It does **not** do structured
extraction, statement normalization, arithmetic derivation, or QC; announcements stay as
unstructured text sources with ticker/date metadata.

Inputs:

- `https://www.psx.com.pk/psx/announcement/corporate-announcements` for the request token
- `https://www.psx.com.pk/psx/Announcement/corporate-search` for per-day rows
- PSX file endpoints under `/psx/files` and `/psx/files-attachment`

Outputs:

- local files under `announcement_documents/{ticker}/{year}/`
- full parsed Gemini markdown under `announcement_markdown/{ticker}/{year}/`
- `artifacts/20_publish_announcements/psx_announcements_{scope}_{start}_{end}_{ts}.sql`
- `artifacts/20_publish_announcements/psx_announcements_{scope}_{start}_{end}.jsonl`
- `artifacts/reports/20_publish_announcements_parse_coverage.{json,md}` when operators audit
  markdown coverage and persistent parse failures
- D1 `psx_announcements` rows with ticker, company, date/time, title, PSX ids, source/direct
  URLs, local path, content type, a bounded markdown excerpt, and `search_text`
- D1 `psx_announcements_fts` rows keyed by `psx_announcements.id`
- R2 announcement attachments under `ANNOUNCEMENTS/{ticker}/{year}/{file}`
- R2 first-page thumbnails under `ANNOUNCEMENT_THUMBNAILS/{ticker}/{year}/{file}.jpg`

`search_text` is the BM25 discovery surface, not a second copy of the announcement body. It
includes explicit metadata tags such as `ticker:MARI` and `date:2026-06-24`, notice status,
the announcement title, and a short deterministic summary. Full markdown can exceed D1's
per-statement limit, so the complete Gemini output remains in `announcement_markdown/` and D1
stores bounded retrieval text in `text`.

```bash
.venv/bin/python pipeline/20_publish_announcements.py --start-date 2026-01-01 --end-date 2026-06-24 --download-only --parse-markdown --workers 20
.venv/bin/python pipeline/20_publish_announcements.py --start-date 2026-01-01 --end-date 2026-06-24 --push-d1
.venv/bin/python pipeline/20_publish_announcements.py --start-date 2026-01-01 --end-date 2026-06-24 --download-only --upload-assets --workers 50
python3 run.py --chain announcements --start-date 2026-01-01 --end-date 2026-06-24 --download-only --parse-markdown --workers 20
python3 run.py --chain announcements --start-date 2026-01-01 --end-date 2026-06-24 --push-d1 --upload-assets
```

The first milestone for a new announcement backfill is file and markdown coverage: run with
`--download-only`, inspect `artifacts/reports/20_publish_announcements.json`, and confirm
`download_failures = 0`, `date_fetch_failures = 0`, and acceptable markdown parse methods.
In download-only mode, the step writes the JSONL evidence, local files, optional markdown, and
optional R2 assets, but does not generate SQL or touch D1. `--pdf-only` exists only as a
debugging filter; normal announcement ingestion should keep GIF/image notices too. Asset
publishing uploads every local announcement attachment via the R2 S3 API. PDFs also get
first-page thumbnails; image notices use the original hosted image as their preview in serve.
The asset publisher merges generated rows with files under `announcement_documents/` so a
narrower JSONL artifact cannot leave downloaded D1-backed attachments unpublished. If thumbnail
rendering fails for a malformed PDF, the raw PDF is still uploaded and the thumbnail error is
recorded in the report.

Current historical scope in production is January 1, 2026 onward. Backfill should follow the
artifact-first sequence used by the filing-page corpus: parse/download the whole scope, audit
coverage and persistent failures, then generate/upload SQL for the same scope. The D1 publish
will skip parse-failure placeholders by default and report `parse_failures_skipped_from_sql`.

Ongoing ingest should be incremental and overlapping, so late PSX postings are picked up
without duplicating local files:

```bash
.venv/bin/python pipeline/20_publish_announcements.py --days 3 --universe-only --download-only --parse-markdown --reset-markdown --workers 3
```

Announcement ingest does not replace the existing annual/quarterly financial filing ingest.
New filings still enter through Step 01 (`01_download.py`) and the document publish chain.

The generated SQL is idempotent for the requested ticker/date window: it deletes the existing
announcement rows and FTS entries for that scope, reinserts the downloaded rows, and rebuilds
the matching FTS entries. Use explicit ticker/date scopes for D1 pushes; do not treat this as
part of the statement spine or the filing-page source corpus.

---

## Re-parsing broken pages (`02_parse.py --reparse-broken`)

Gemini's parse occasionally enters a repetition loop, emitting a huge garbled page (observed
up to ~1.9MB vs a ~3KB norm). These often pass review (the DeepSeek `search_text` summary is
still sane), so the corruption lives only in the raw markdown `text` — it bloats
`financial_documents_new.text` and can trip `SQLITE_TOOBIG` on publish. Because `search_text`
is unaffected, **only the markdown needs re-parsing; no re-tag is required.**

`02_parse.py --reparse-broken` detects broken pages (markdown over `--max-bytes`, default
60,000, OR a single non-empty line repeated more than `--max-repeat` times, default 50; raise
to ~100 to skip legitimately sparse tables) and re-parses them (forces `--reset`). It writes
the affected tickers to `artifacts/reports/02_parse.json`.

```bash
python3 pipeline/02_parse.py --reparse-broken --max-repeat 100
```

After re-parsing, **re-publish the affected tickers** so `financial_documents_new.text` picks
up the clean markdown (`17_publish_documents.py --ticker <T> --push-d1`). Assets are unchanged
(the page PDF/thumbnail is the same source). Step 17 also caps stored `text` at 60,000 chars
as a backstop against any residual oversized page.

---

## Macro Documents (auxiliary track, out-of-band)

Purpose: ingest macroeconomic source documents (e.g. the State Bank economic survey) into a
separate retrieval-only surface, independent of the per-ticker financial-statement spine.

Script: `macro_push.py` — deterministic. Reads Gemini-parsed markdown from
`macro_markdown/{source_dir}/{chapter}/page_NNN.md`, respects the `03_gate.py`
`skip_manifest.json` using the historical `macro/...` skip keys, tags each surviving page as
`table` or `text`, and uploads to the D1 tables `macro_documents` + `macro_fts`.

```bash
python pipeline/macro_push.py                      # all macro docs
python pipeline/macro_push.py --doc economic_survey
python pipeline/macro_push.py --sql-only           # emit SQL without uploading
python pipeline/macro_push.py --assets-only --workers 50
```

Outputs: D1 tables `macro_documents`, `macro_fts`; R2 source assets under
`MACRO_PAGES/...` and `MACRO_THUMBNAILS/...`; `artifacts/macro_push/macro_{ts}.sql`.

Local macro corpus folders intentionally live outside the company filing folders:

- `macro_documents/{source_doc}/...pdf` stores original macro PDFs.
- `macro_pages/{source_doc}/{chapter}/page_NNN.pdf` stores split page PDFs.
- `macro_markdown/{source_doc}/{chapter}/page_NNN.md` stores Gemini page markdown.
- `macro_thumbnails/{source_doc}/{chapter}/page_NNN.jpg` stores source-panel thumbnails.
- `macro_review/{source_doc}/{chapter}.jsonl` stores review/tag evidence.

Do not put macro sources under `database_pdfs/`, `pdf_pages/`, `markdown_pages/`, or
`pdf_thumbnails/`; those roots are for per-company filing pages.

Notes:
- Macro page markdown is produced by the same Gemini parse model as step 02 (run against the
  macro PDFs); `macro_push.py` itself does no model calls. This is why the "three model
  touchpoints" invariant still holds for the statement spine — macro adds another *use* of
  parse, not a new model role.
- Like `13_d1_push.py` and `15_ratios.py`, this step is **not** part of any `run.py` chain; it
  is run manually after its inputs exist. The same D1 import-lock warning from step 13b
  applies to its upload.
- The serve runtime queries `macro_documents` / `macro_fts` through a dedicated macro
  narrative lane. Source previews require the matching R2 assets:
  `https://source.psxgpt.com/MACRO_PAGES/{source_doc}/{chapter}/page_NNN.pdf` and
  `https://source.psxgpt.com/MACRO_THUMBNAILS/{source_doc}/{chapter}/page_NNN.jpg`.

---

## The Two Control Loops

The pipeline is a linear spine with two bounded, re-entrant loops. The extract loop escalates
into the parse loop when the source is the culprit.

```text
              ┌─────────────── re-parse page (cap N) ───────────────┐
              │                                                      │
  PARSE ──md──▶ REVIEW ──tags+basis+score──▶ route ──▶ EXTRACT ──▶ QC ──ok──▶ publish
 (Flash Lite)    │                                                   │
                 │ score = ReOCR / garbled?                        fail
                 └──────────────▲                                    │
            (review IS the parse QC)                      diagnose layer:
                                                          ├─ extraction slip → re-EXTRACT (cap N)
                                                          ├─ bad source      → re-PARSE → re-REVIEW → re-EXTRACT
                                                          ├─ systematic      → fix pack (human) → re-EXTRACT
                                                          └─ still failing   → QUARANTINE
```

**Loop A — Parse ⇄ Review (step 05).** The review pass *is* the parse QC.
`extraction_score`: `OK` proceeds; `Fix` is a deterministic text cleanup; `ReOCR` re-parses
the page and re-reviews it, capped at N attempts, then quarantines. The unit is the page.

**Loop B — Extract ⇄ Extraction QC.** A QC failure is diagnosed to a layer, and the fix
differs by layer. The critical case is **bad source**: the value is wrong *in the markdown*,
so the fix is re-parse, not re-extract — Loop B cascades into Loop A.

Loop invariants: a per-loop iteration cap; scope narrows to failing items each pass; capped
items are quarantined, never silently passed; provenance (`page → parse → review → extract`)
selects the re-entry layer mechanically.

---

## run.py Chains

`run.py` wires stages into named chains:

```text
download   → 01_download
parse      → 01_ingest → 02_parse → 03_gate → 04_tag → 05_fix → 06_route
pl         → parse + 08_extract(pl) → 09_qc → 10_normalize → 11_derive → 12_flatten
bs         → parse + 08_extract(bs) → 09_qc → 10_normalize → 12_flatten
cf         → parse + 08_extract(cf) → 09_qc → 10_normalize → 11_derive → 12_flatten
extract    → parse + pl + bs + cf extract stages
publish    → 13_publish
reparse    → 02_parse --reparse-broken
documents  → 17_publish_documents → 18_publish_assets
announcements → 20_publish_announcements
triage     → 14_triage
ratios     → 15_ratios
market_qc  → 16_market_qc
state      → 19_pipeline_state
full       → parse + extract + publish
```

D1 upload (`13_d1_push.py`), ratios rebuild (`15_ratios.py`), and the document corpus publish
(`documents` chain) are all run after the `full` chain completes. The `documents` chain runs
17_publish_documents → 18_publish_assets; it is SQL-only and skips the R2 upload unless
`--push-d1` (write D1 rows) and `--upload-assets` (run 18_publish_assets) are passed. All three
must be run together for new tickers — publishing statements/ratios without the document
corpus leaves answers without source previews. Full new-ticker publish:

```bash
python3 run.py --chain documents --ticker FECTC --push-d1 --upload-assets
```

---

## Model, Cost, and Caching Policy

Model choices are implementation details; the policy is durable.

- **Parse**: Gemini Flash Lite (`gemini-3.1-flash-lite`, env: `GEMINI_PARSE_MODEL`).
- **Review + Extract**: DeepSeek V4 Pro (`deepseek-v4-pro`, env: `DEEPSEEK_REVIEW_MODEL` /
  `DEEPSEEK_EXTRACT_MODEL`, base URL: `DEEPSEEK_API_BASE`, default
  `https://api.deepseek.com`). The legacy `deepseek-chat` / `deepseek-reasoner` aliases are
  deprecated 2026-07-24 and must not be the configured default past then.
- **Caching is automatic and prefix-exact.** Keep every prompt's instruction block a stable
  prefix with the variable page body last; never interpolate variable data into the prefix. A
  cache hit costs ~50–120× less than a miss, so the page body (unique, uncacheable) dominates
  cost — which is why merging two calls into one review pass is the real saving.
- **Limits are concurrency-only** (no RPM/TPM): v4-pro 500, v4-flash 2500. Retry logic must
  special-case HTTP 429 with backoff, distinct from content errors.
- **Cost is computed by code** from provider usage fields and a local price table (provider,
  model, effective date, input/output price, batch discount, source URL). Every model output
  records: pack version/prompt hash, input artifact hash, output artifact hash, model name,
  provider request id, usage/cost row.

---

## Durable Choices

- Offline, source-linked, auditable; D1 receives audited outputs only.
- Three model roles in the statement spine; auxiliary retrieval corpora may reuse parse-style model calls.
- The page is read once; structure and summary come from one review pass.
- Tags route; only routing tags exist; statements are named with basis at first pass.
- LLMs extract reported values; code derives Q4, LTM, ratios.
- Every gate is a bounded loop; QC failures become clustered, priced repair decisions.
- New prompts/models are validated against trusted existing extractions.
- Runtime metadata records tokens and cost deterministically.

## Changeable Choices

- Which model powers parse, review, or extract.
- Exact tag names as new extraction surfaces (operational metrics by sector) are added.
- The auto-vs-ask cost/confidence thresholds.
- External comparison sources and ratio formulas as conventions standardize.
- Additional QC checks (source match, units, collision) as they are implemented.

## Where To Go Next

- For a quick operator summary, read `README.md`.
- For a source-linked numeric defect, inspect the relevant artifacts in order: parse markdown,
  review JSONL, route manifest, extracted markdown, QC failures, normalized JSON, flat rows, ratio
  output, publish report, then Step 19 state.
- For a retrieval-only defect, inspect the relevant corpus track: filing pages in Step 17, source
  assets in Step 18, announcements in Step 20, or macro documents in the macro track.
- For a request that has correct corpus data but wrong routing, SQL, compute, wording, trace, or
  UI source grouping, switch to `psxgpt-serve/apps/analysis/spec.md`.
