# psxGPT Pipeline — Gap Analysis

_What's missing in the download → parse → load → accuracy loop, how it was found, and what to do about it._

## Purpose

The product thesis is settled (a trustworthy Pakistan research system; power users have
confirmed willingness to pay). The blocker is operational: the pipeline must reliably
**download, parse, and load data while guaranteeing the numbers are accurate**, without a
human operating it step by step. This document records the gaps in that loop, the evidence
behind each, and the fixes — separating what was already done from what remains.

---

## How this was figured out (process)

The gaps were not derived from theory; they were found by interrogating the live system and
its own artifacts:

1. **Started from a real failure.** A user realized newly added tickers weren't searchable.
   Rather than guess, traced where `financial_documents_new` is populated.
2. **Diffed the live D1 state across surfaces.** Queried distinct tickers per table:
   `financial_statements` = 471, `financial_documents_new` = 138, `ratios` = 471. A 333-ticker
   gap that no dashboard or report was showing.
3. **Read the stage that owns the gap** (`17_publish_documents.py`) and the runner (`run.py`)
   and found it was a manual, per-ticker step, absent from every chain and from the spec.
4. **Checked the inputs existed** before assuming re-work: confirmed `review/` + `markdown_pages/`
   + thumbnails + industry data were present for all 333 missing tickers (they were — only the
   publish/upload step had been skipped).
5. **Inspected the accuracy machinery directly**: read `09_qc.py` (only schema + formula checks
   implemented), counted open failures (`artifacts/qc_failures.jsonl` = 96), and sampled them —
   they were real number errors (scale/units and digit transpositions), not noise. Checked
   `16_market_qc` output: it had validated **1 ticker**, not the universe.
6. **Measured corpus depth**: 472 tickers, 4,288 filings, **median 5 filings/ticker** — shallow
   for research.
7. **Tested the "we already have observability" claim** by reading the actual report artifacts.
   Found most are **per-run aggregates** (`02_parse.json` = `{parsed, failed}`,
   `13_d1_push.json` = `{total_rows}`), while only `01_ingest` and `15_ratios` emit per-entity
   coverage. No artifact joins stages into a per-ticker, end-to-end status.

The throughline: **every per-stage report was green, yet the system was inconsistent.** The
defect lived in the *seam between stages*, which no single-stage report can see. That is the
core finding.

---

## The gaps

### Gap 1 — No cross-stage, per-entity state (the root cause)

**Symptom:** the 333-ticker document gap sat undetected. `13_d1_push.json` reported "pushed
PL/BS/CF — success"; `17`'s report showed only its last ticker. Both green; the system wrong.

**Diagnosis:** the pipeline emits **logs** (what each step *did* on its last run), not **state**
(what each entity *is* across the whole pipeline). Reports are siloed by stage. Nothing answers
*"for ticker X — downloaded? parsed-clean? extracted? in statements? in documents? ratios
fresh? assets up? stale?"* Seam-drift is therefore invisible.

**Evidence:** `02_parse.json` / `13_d1_push.json` / `17_publish_documents.json` are per-run
aggregates; `01_ingest` and `15_ratios` already emit per-entity coverage (so the capability
exists, just not uniformly).

### Gap 2 — The accuracy loop accumulates failures instead of converging

**Symptom:** 96 unresolved formula-reconciliation failures sitting in `qc_failures.jsonl`, e.g.:
- `AGIC` PL: expected `-840`, got `-839,892` → **1000× units/scale error**
- `AGP` PL: expected `-4,990,019`, got `-4,890,099` → **digit transposition** (parse error)
- `AGP` PL: `25,044,770` vs `17,264,750` → wrong/missing component

**Diagnosis:** `09_qc` implements only **schema + formula** checks. The spec's planned checks —
**source-match** (does the value appear on the cited page), **units/period consistency**,
**collision** — are not implemented. The triage→re-extract loop (`14`) exists but isn't run to
completion, so failures pile up rather than being driven to zero or quarantined.

**Why it matters:** "every number is accurate and traces to its source" is the product's moat.
Source-match is that guarantee, and it's the missing check.

### Gap 3 — External accuracy validation is effectively off

**Symptom:** `16_market_qc` (cross-check computed P/E & dividend yield against live prices)
ran against **1 ticker**.

**Diagnosis:** the cheapest external accuracy signal — does our math match observable market
reality — is not running across the universe.

### Gap 4 — Loading is manual, forgettable, and can take down serving

**Symptom:** the publish steps (`13_d1_push`, `15_ratios`, `17_publish_documents`,
`18_publish_assets`) are separate manual commands, none in a single chain until this session.
Separately, a D1 import locked the database for ~22 min and caused live `D1_ERROR` failures.

**Diagnosis:** no idempotent "make D1 current" that runs the publish steps in order, verifies
invariants, and uses a staging-table + atomic swap so a load never blocks reads.

### Gap 5 — Corpus depth is too shallow for research

**Symptom:** median **5 filings/ticker** (~1–2 years), 12 tickers with <4 filings.

**Diagnosis:** acquisition (`01_download`) and coverage (`01_ingest`) report gaps but don't
drive automated historical backfill or detect newly published filings incrementally. A research
system needs 5–10 years of history.

---

## Fixes

### Already done this session

- **Closed the 333-ticker document gap.** `financial_documents_new` now 471/471 tickers
  (252,528 rows; 57,836 added), `filings_fts_new` rebuilt, 141,750 R2 assets uploaded.
- **Restored deterministic thumbnails** at the correct spec (360px/q70/110dpi).
- **Fixed garbled pages**: `02_parse.py --reparse-broken` detects repetition-loop garble;
  322 pages re-parsed clean and affected tickers re-published. Added a 60 KB `text` cap in
  `17` as a `SQLITE_TOOBIG` backstop.
- **Made the loop runnable end-to-end**: `run.py` `documents` chain
  (`17_publish_documents → 18_publish_assets`), `--gap` modes that diff against live state,
  resumable. Consolidated scripts (no file bloat). Documented in spec, SKILLS, `.env.example`
  (incl. the R2 S3-vs-REST credential trap).

### Still to do (in priority order)

1. **Reconciliation view = the state ledger (Gap 1).** Highest leverage. Two parts:
   (a) make the run-aggregate stages (`02_parse`, `13_d1_push`, `17`) also write **per-ticker
   status**, as `01_ingest`/`15_ratios` already do; (b) add **one view** that joins those +
   queries D1 and flags seam-drift (`statements ⊄ documents`, ratios stale, assets missing).
   This is an aggregation layer over outputs that already exist — not a new system. It would
   have caught this session's gap on day one.
2. **Close the accuracy loop (Gap 2).** Drive the 96 formula failures → 0 or quarantine via
   `14`'s triage→re-extract. Implement **source-match** and **unit/scale normalization** (the
   `AGIC` 1000× class) in `09_qc`.
3. **Scale `16_market_qc` to the full universe (Gap 3).** Cheap external accuracy signal,
   currently near-off.
4. **Atomic, idempotent load (Gap 4).** One "make D1 current" command driven by the ledger,
   gated by invariants, using staging-table + atomic swap so loads never lock serving.
5. **Deepen history + incremental acquisition (Gap 5).** Once the loop is trustworthy, backfill
   5–10 years/ticker and detect new filings as published.

---

## The one-line conclusion

The accelerator is **not faster scripts** — the stages are already parallel. It is a
**ledger-driven control loop with the QC loop actually closed**: a per-entity status that spans
stages so drift is caught in the seams, and an accuracy gate that converges to zero instead of
accumulating. That is what turns a lab the founder operates into a system that keeps itself
complete, fresh, and accurate.
