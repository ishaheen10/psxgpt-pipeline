#!/usr/bin/env python3
"""Step 19 — Build the cross-stage pipeline state ledger.

This deterministic control-plane view recomputes per-ticker state from local
artifacts plus production D1. It catches seam drift that single-stage run
reports cannot see.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import ARTIFACTS, MARKDOWN_ROOT, PROJECT_ROOT  # noqa: E402
from shared.report import Action, emit_decision, stable_id, write_jsonl, write_report  # noqa: E402

D1_DB = "psx"
STATE_DIR = ARTIFACTS / "state"
QUALITY_DIR = ARTIFACTS / "quality"
QC_FAILURES = ARTIFACTS / "qc_failures.jsonl"
ISSUES = ARTIFACTS / "issues" / "issues.jsonl"
FLAT_DIR = PROJECT_ROOT / "data" / "flat"
REVIEW_ROOT = PROJECT_ROOT / "review"
OPEN_ERROR_QUEUE = STATE_DIR / "open_error_queue.jsonl"
ASSET_MANIFESTS = [
    ARTIFACTS / "18_publish_assets" / "manifest.json",
    ARTIFACTS / "r2_upload" / "manifest.json",
]
MARKET_QC_REPORTS = [
    ARTIFACTS / "16_market_qc" / "market_qc.json",
    ARTIFACTS / "reports" / "16_market_qc.json",
]


@dataclass
class TickerState:
    ticker: str
    industry: str = ""
    local_markdown_pages: int = 0
    local_review_rows: int = 0
    local_flat_rows: dict[str, int] = field(default_factory=dict)
    open_qc_failures: int = 0
    stage_issues: dict[str, int] = field(default_factory=dict)
    blocker_stage_issues: dict[str, int] = field(default_factory=dict)
    d1_statement_rows: int = 0
    d1_statement_types: int = 0
    d1_latest_statement_period: str | None = None
    d1_document_rows: int = 0
    d1_document_filings: int = 0
    d1_fts_rows: int = 0
    d1_document_rows_missing_jpg_path: int = 0
    d1_ratio_rows: int = 0
    d1_ltm_ratio_rows: int = 0
    d1_latest_ratio_period: str | None = None
    d1_market_rows: int = 0
    d1_latest_market_date: str | None = None
    d1_latest_market_cap: float | None = None
    market_qc_status: str | None = None
    market_qc_stale_days: int | None = None
    market_qc_notes: list[str] = field(default_factory=list)
    asset_pdf_pages: int = 0
    asset_thumbnails: int = 0
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommended_next_commands: list[str] = field(default_factory=list)


def load_ticker_universe() -> dict[str, str]:
    data = json.loads((PROJECT_ROOT / "tickers100.json").read_text())
    return {
        str(row.get("Symbol", "")).strip().upper(): str(row.get("Industry", "")).strip()
        for row in data
        if row.get("Symbol")
    }


def parse_wrangler_json(stdout: str) -> list[list[dict[str, Any]]]:
    start = stdout.find("[")
    if start < 0:
        return []
    data = json.loads(stdout[start:])
    blocks: list[list[dict[str, Any]]] = []
    if isinstance(data, list):
        for block in data:
            if isinstance(block, dict) and isinstance(block.get("results"), list):
                blocks.append(block["results"])
    return blocks


def d1_query(sql: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", "--json", "--command", sql],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"D1 query failed:\n{result.stderr[-2000:]}")
    rows: list[dict[str, Any]] = []
    for block in parse_wrangler_json(result.stdout):
        rows.extend(block)
    return rows


def count_markdown_pages() -> Counter[str]:
    counts: Counter[str] = Counter()
    if not MARKDOWN_ROOT.exists():
        return counts
    for path in MARKDOWN_ROOT.glob("*/*/*/page_*.md"):
        counts[path.relative_to(MARKDOWN_ROOT).parts[0].upper()] += 1
    return counts


def count_review_rows() -> Counter[str]:
    counts: Counter[str] = Counter()
    if not REVIEW_ROOT.exists():
        return counts
    for path in REVIEW_ROOT.glob("*/*/*.jsonl"):
        ticker = path.relative_to(REVIEW_ROOT).parts[0].upper()
        for line in path.read_text(errors="replace").splitlines():
            if line.strip():
                counts[ticker] += 1
    return counts


def count_flat_rows() -> dict[str, Counter[str]]:
    by_type: dict[str, Counter[str]] = {stmt: Counter() for stmt in ("pl", "bs", "cf")}
    for stmt in ("pl", "bs", "cf"):
        path = FLAT_DIR / f"{stmt}.jsonl"
        if not path.exists():
            continue
        for line in path.open():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ticker = str(row.get("ticker") or "").upper()
            if ticker:
                by_type[stmt][ticker] += 1
    return by_type


def count_stage_issues() -> tuple[dict[str, Counter[str]], dict[str, Counter[str]], Counter[str], Counter[str]]:
    by_ticker: dict[str, Counter[str]] = {}
    blockers_by_ticker: dict[str, Counter[str]] = {}
    by_stage: Counter[str] = Counter()
    blockers_by_stage: Counter[str] = Counter()
    if not ISSUES.exists():
        return by_ticker, blockers_by_ticker, by_stage, blockers_by_stage
    for line in ISSUES.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ticker = str(row.get("ticker") or "").upper()
        stage = str(row.get("stage") or "unknown")
        severity = str(row.get("severity") or "warning")
        by_stage[stage] += 1
        if severity == "blocker":
            blockers_by_stage[stage] += 1
        if ticker:
            by_ticker.setdefault(ticker, Counter())[stage] += 1
            if severity == "blocker":
                blockers_by_ticker.setdefault(ticker, Counter())[stage] += 1
    return by_ticker, blockers_by_ticker, by_stage, blockers_by_stage


def load_stage_issue_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not ISSUES.exists():
        return rows
    for line in ISSUES.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def count_qc_failures() -> Counter[str]:
    counts: Counter[str] = Counter()
    if not QC_FAILURES.exists():
        return counts
    seen_ids: set[str] = set()
    for line in QC_FAILURES.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        failure_id = str(row.get("id") or "").strip()
        if failure_id and failure_id in seen_ids:
            continue
        if failure_id:
            seen_ids.add(failure_id)
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            counts[ticker] += 1
    return counts


def count_asset_manifest() -> tuple[Counter[str], Counter[str]]:
    pdfs: Counter[str] = Counter()
    thumbs: Counter[str] = Counter()
    manifest = next((path for path in ASSET_MANIFESTS if path.exists()), None)
    if not manifest:
        return pdfs, thumbs
    data = json.loads(manifest.read_text())
    for key in data.get("uploaded_keys", []):
        parts = str(key).split("/")
        if len(parts) < 2:
            continue
        prefix, ticker = parts[0], parts[1].upper()
        if prefix == "PDF_PAGES":
            pdfs[ticker] += 1
        elif prefix == "PDF_THUMBNAILS":
            thumbs[ticker] += 1
    return pdfs, thumbs


def load_market_qc_rows() -> dict[str, dict[str, Any]]:
    report = next((path for path in MARKET_QC_REPORTS if path.exists()), None)
    if not report:
        return {}
    try:
        data = json.loads(report.read_text())
    except json.JSONDecodeError:
        return {}
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("ticker") or "").upper(): row
        for row in rows
        if isinstance(row, dict) and row.get("ticker")
    }


def keyed(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["ticker"]).upper(): row for row in rows if row.get("ticker")}


def load_d1_state() -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "statements": keyed(d1_query("""
            SELECT ticker,
                   COUNT(*) AS statement_rows,
                   COUNT(DISTINCT statement_type) AS statement_types,
                   MAX(period_end) AS latest_statement_period,
                   SUM(CASE WHEN source_url IS NULL OR source_url='' THEN 1 ELSE 0 END) AS missing_source_url_rows,
                   SUM(CASE WHEN source_pages IS NULL OR source_pages='' THEN 1 ELSE 0 END) AS missing_source_pages_rows
            FROM financial_statements
            GROUP BY ticker
            ORDER BY ticker;
        """)),
        "documents": keyed(d1_query("""
            SELECT ticker,
                   COUNT(*) AS document_rows,
                   COUNT(DISTINCT filing_type || ':' || filing_period) AS document_filings,
                   SUM(CASE WHEN jpg_path IS NULL OR jpg_path='' THEN 1 ELSE 0 END) AS missing_jpg_path_rows,
                   SUM(CASE WHEN search_text IS NULL OR search_text='' THEN 1 ELSE 0 END) AS missing_search_text_rows,
                   SUM(CASE WHEN text IS NULL OR text='' THEN 1 ELSE 0 END) AS missing_text_rows
            FROM financial_documents_new
            GROUP BY ticker
            ORDER BY ticker;
        """)),
        "fts": keyed(d1_query("""
            SELECT d.ticker, COUNT(f.rowid) AS fts_rows
            FROM financial_documents_new d
            LEFT JOIN filings_fts_new f ON f.rowid = d.id
            GROUP BY d.ticker
            ORDER BY d.ticker;
        """)),
        "ratios": keyed(d1_query("""
            SELECT ticker,
                   COUNT(*) AS ratio_rows,
                   SUM(CASE WHEN period_duration='LTM' THEN 1 ELSE 0 END) AS ltm_ratio_rows,
                   MAX(period_end) AS latest_ratio_period
            FROM ratios
            GROUP BY ticker
            ORDER BY ticker;
        """)),
        "market": keyed(d1_query("""
            WITH latest AS (
              SELECT ticker, date, closing_price, total_shares, market_cap,
                     ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
              FROM market_data
            )
            SELECT ticker,
                   COUNT(*) AS market_rows,
                   MAX(CASE WHEN rn=1 THEN date END) AS latest_market_date,
                   MAX(CASE WHEN rn=1 THEN market_cap END) AS latest_market_cap,
                   SUM(CASE WHEN closing_price IS NULL OR closing_price <= 0 THEN 1 ELSE 0 END) AS bad_price_rows,
                   SUM(CASE WHEN total_shares IS NULL OR total_shares <= 0 THEN 1 ELSE 0 END) AS bad_share_rows,
                   SUM(CASE WHEN market_cap IS NULL OR market_cap <= 0 THEN 1 ELSE 0 END) AS bad_market_cap_rows
            FROM latest
            GROUP BY ticker
            ORDER BY ticker;
        """)),
    }


def add_reason(state: TickerState, reason: str, command: str | None = None, warning: bool = False) -> None:
    target = state.warnings if warning else state.blocking_reasons
    if reason not in target:
        target.append(reason)
    if command and command not in state.recommended_next_commands:
        state.recommended_next_commands.append(command)


def build_state() -> tuple[list[TickerState], dict[str, Any]]:
    universe = load_ticker_universe()
    markdown = count_markdown_pages()
    review = count_review_rows()
    flat = count_flat_rows()
    qc_failures = count_qc_failures()
    stage_issues, blocker_stage_issues, issue_counts_by_stage, blocker_issue_counts_by_stage = count_stage_issues()
    asset_pdfs, asset_thumbs = count_asset_manifest()
    market_qc = load_market_qc_rows()
    d1 = load_d1_state()

    all_tickers = set(universe)
    for group in d1.values():
        all_tickers.update(group.keys())
    all_tickers.update(ticker for ticker in markdown if ticker in universe)
    all_tickers.update(ticker for ticker in review if ticker in universe)
    all_tickers.update(ticker for ticker in qc_failures if ticker in universe)
    all_tickers.update(ticker for ticker in stage_issues if ticker in universe)
    all_tickers.update(ticker for ticker in market_qc if ticker in universe)
    for counts in flat.values():
        all_tickers.update(ticker for ticker in counts if ticker in universe)

    states: list[TickerState] = []
    for ticker in sorted(all_tickers):
        st = TickerState(ticker=ticker, industry=universe.get(ticker, ""))
        st.local_markdown_pages = int(markdown[ticker])
        st.local_review_rows = int(review[ticker])
        st.local_flat_rows = {stmt: int(flat[stmt][ticker]) for stmt in ("pl", "bs", "cf")}
        st.open_qc_failures = int(qc_failures[ticker])
        st.stage_issues = dict(stage_issues.get(ticker, Counter()))
        st.blocker_stage_issues = dict(blocker_stage_issues.get(ticker, Counter()))
        st.asset_pdf_pages = int(asset_pdfs[ticker])
        st.asset_thumbnails = int(asset_thumbs[ticker])

        fs = d1["statements"].get(ticker, {})
        docs = d1["documents"].get(ticker, {})
        fts = d1["fts"].get(ticker, {})
        ratios = d1["ratios"].get(ticker, {})
        market = d1["market"].get(ticker, {})
        market_qc_row = market_qc.get(ticker, {})

        st.d1_statement_rows = int(fs.get("statement_rows") or 0)
        st.d1_statement_types = int(fs.get("statement_types") or 0)
        st.d1_latest_statement_period = fs.get("latest_statement_period")
        st.d1_document_rows = int(docs.get("document_rows") or 0)
        st.d1_document_filings = int(docs.get("document_filings") or 0)
        st.d1_document_rows_missing_jpg_path = int(docs.get("missing_jpg_path_rows") or 0)
        st.d1_fts_rows = int(fts.get("fts_rows") or 0)
        st.d1_ratio_rows = int(ratios.get("ratio_rows") or 0)
        st.d1_ltm_ratio_rows = int(ratios.get("ltm_ratio_rows") or 0)
        st.d1_latest_ratio_period = ratios.get("latest_ratio_period")
        st.d1_market_rows = int(market.get("market_rows") or 0)
        st.d1_latest_market_date = market.get("latest_market_date")
        st.d1_latest_market_cap = market.get("latest_market_cap")
        st.market_qc_status = market_qc_row.get("status")
        st.market_qc_stale_days = market_qc_row.get("stale_days")
        st.market_qc_notes = list(market_qc_row.get("notes") or [])

        if st.open_qc_failures:
            add_reason(st, "open_extraction_qc_failures", "python3 pipeline/14_triage.py")
        if st.blocker_stage_issues:
            stages = ",".join(sorted(st.blocker_stage_issues))
            add_reason(st, f"blocking_stage_issues:{stages}", "cat artifacts/issues/issues.jsonl")
        if st.d1_statement_rows == 0:
            if any(st.local_flat_rows.values()):
                add_reason(st, "flat_rows_not_published_to_d1", f"python3 pipeline/13_d1_push.py --ticker {ticker}")
            else:
                add_reason(st, "missing_financial_statement_rows", f"python3 run.py --chain extract --ticker {ticker}")
        if 0 < st.d1_statement_types < 3:
            add_reason(st, "partial_statement_type_coverage", f"python3 run.py --chain extract --ticker {ticker}")
        if st.d1_statement_rows > 0 and st.d1_document_rows == 0:
            add_reason(st, "missing_document_corpus_rows", f"python3 run.py --chain documents --ticker {ticker} --push-d1 --upload-assets")
        if st.d1_document_rows > 0 and st.d1_fts_rows != st.d1_document_rows:
            add_reason(st, "fts_document_row_mismatch", f"python3 pipeline/17_publish_documents.py --ticker {ticker} --push-d1")
        if st.d1_document_rows_missing_jpg_path:
            add_reason(st, "document_rows_missing_jpg_path", f"python3 pipeline/17_publish_documents.py --ticker {ticker} --push-d1")
        if st.d1_document_rows > 0 and st.asset_pdf_pages < st.d1_document_rows:
            add_reason(st, "asset_manifest_does_not_cover_page_pdfs", f"python3 pipeline/18_publish_assets.py --tickers {ticker}", warning=True)
        if st.d1_document_rows > 0 and st.asset_thumbnails < st.d1_document_rows:
            add_reason(st, "asset_manifest_does_not_cover_thumbnails", f"python3 pipeline/18_publish_assets.py --tickers {ticker}", warning=True)
        if st.d1_statement_rows > 0 and st.d1_ratio_rows == 0:
            add_reason(st, "missing_ratios", "python3 pipeline/15_ratios.py")
        if st.d1_statement_rows > 0 and st.d1_market_rows == 0:
            add_reason(st, "missing_market_data", f"python3 pipeline/16_market_data.py --ticker {ticker} --push-d1")
        if int(market.get("bad_price_rows") or 0) or int(market.get("bad_share_rows") or 0) or int(market.get("bad_market_cap_rows") or 0):
            add_reason(st, "bad_market_data_values", f"python3 pipeline/16_market_data.py --ticker {ticker} --push-d1")
        if "psx_latest_row_is_stale" in st.market_qc_notes:
            add_reason(
                st,
                "stale_psx_market_price",
                f"python3 pipeline/16_market_data.py --ticker {ticker} --push-d1 && python3 pipeline/16_market_qc.py --ticker {ticker}",
                warning=True,
            )
        if st.d1_document_rows > 0 and st.local_review_rows == 0:
            add_reason(st, "d1_documents_without_local_review_rows", None, warning=True)
        if st.d1_statement_rows > 0 and int(fs.get("missing_source_url_rows") or 0):
            add_reason(st, "statement_rows_missing_source_url", f"python3 pipeline/10_normalize.py --ticker {ticker}", warning=True)
        # Blank search_text on effectively-empty pages is expected (~6% of rows corpus-wide).
        # Only flag tickers where a high proportion is blank — a real tagging problem.
        missing_search = int(docs.get("missing_search_text_rows") or 0)
        if st.d1_document_rows > 0 and missing_search > 0.5 * st.d1_document_rows:
            add_reason(st, "document_rows_mostly_missing_search_text", f"python3 pipeline/04_tag.py --ticker {ticker}", warning=True)

        states.append(st)

    blocking_counts = Counter(reason for st in states for reason in st.blocking_reasons)
    warning_counts = Counter(reason for st in states for reason in st.warnings)
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "tickers": len(states),
        "blocking_tickers": sum(1 for st in states if st.blocking_reasons),
        "warning_tickers": sum(1 for st in states if st.warnings),
        "d1_statement_tickers": sum(1 for st in states if st.d1_statement_rows > 0),
        "d1_document_tickers": sum(1 for st in states if st.d1_document_rows > 0),
        "d1_ratio_tickers": sum(1 for st in states if st.d1_ratio_rows > 0),
        "d1_market_tickers": sum(1 for st in states if st.d1_market_rows > 0),
        "open_qc_failure_tickers": sum(1 for st in states if st.open_qc_failures > 0),
        "open_qc_failures": sum(st.open_qc_failures for st in states),
        "issue_counts_by_stage": dict(issue_counts_by_stage),
        "blocker_issue_counts_by_stage": dict(blocker_issue_counts_by_stage),
        "blocking_reason_counts": dict(blocking_counts),
        "warning_counts": dict(warning_counts),
    }
    return states, summary


def write_markdown(states: list[TickerState], summary: dict[str, Any]) -> Path:
    out = STATE_DIR / "pipeline_gate.md"
    blocked = [st for st in states if st.blocking_reasons]
    warned = [st for st in states if st.warnings]
    missing_market = [st.ticker for st in states if "missing_market_data" in st.blocking_reasons]
    stale_market = [st.ticker for st in states if "stale_psx_market_price" in st.warnings]
    partial_statement = [st.ticker for st in states if "partial_statement_type_coverage" in st.blocking_reasons]
    lines = [
        "# psxGPT Pipeline State Gate",
        "",
        f"Generated: {summary['run_at']}",
        "",
        "## Summary",
        "",
        f"- Tickers checked: {summary['tickers']}",
        f"- Blocking tickers: {summary['blocking_tickers']}",
        f"- Warning tickers: {summary['warning_tickers']}",
        f"- D1 coverage: statements {summary['d1_statement_tickers']}, documents {summary['d1_document_tickers']}, ratios {summary['d1_ratio_tickers']}, market {summary['d1_market_tickers']}",
        f"- Open extraction QC failures: {summary['open_qc_failures']} across {summary['open_qc_failure_tickers']} tickers",
        f"- Stage issue ledger: {sum(summary.get('issue_counts_by_stage', {}).values())} issues; {sum(summary.get('blocker_issue_counts_by_stage', {}).values())} blockers",
        "",
        "## Stage Issues",
        "",
    ]
    if summary.get("issue_counts_by_stage"):
        for stage, count in sorted(summary["issue_counts_by_stage"].items()):
            blockers = summary.get("blocker_issue_counts_by_stage", {}).get(stage, 0)
            lines.append(f"- `{stage}`: {count} issues ({blockers} blockers)")
    else:
        lines.append("- None")

    lines += [
        "",
        "## Blocking Reasons",
        "",
    ]
    reason_counts = summary["blocking_reason_counts"]
    if reason_counts:
        for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- `{reason}`: {count}")
    else:
        lines.append("- None")

    lines += ["", "## Warning Reasons", ""]
    warning_counts = summary["warning_counts"]
    if warning_counts:
        for reason, count in sorted(warning_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- `{reason}`: {count}")
    else:
        lines.append("- None")

    lines += ["", "## Recommended Rerun Loops", ""]
    if summary["open_qc_failures"]:
        lines.append("- Extraction accuracy loop: `python3 pipeline/14_triage.py`")
    if missing_market:
        lines.append(f"- PSX market loop: `python3 pipeline/16_market_data.py --tickers {','.join(missing_market)} --push-d1`")
    if stale_market:
        lines.append(f"- Stale PSX market audit loop: `python3 pipeline/16_market_qc.py --tickers {','.join(stale_market)}`")
    if partial_statement:
        for ticker in partial_statement:
            lines.append(f"- Statement extraction loop for {ticker}: `python3 run.py --chain extract --ticker {ticker}`")
    if not summary["open_qc_failures"] and not missing_market and not stale_market and not partial_statement:
        lines.append("- None")

    lines += ["", "## Per-Ticker Commands", ""]
    commands = Counter(cmd for st in blocked for cmd in st.recommended_next_commands)
    if commands:
        for command, count in commands.most_common(30):
            lines.append(f"- `{command}` ({count} ticker(s))")
    else:
        lines.append("- None")

    lines += ["", "## Warning Tickers", ""]
    if warned:
        lines.append("| Ticker | Warnings | Latest market date | Stale days | Next command |")
        lines.append("| --- | --- | --- | --- | --- |")
        for st in warned[:200]:
            command = st.recommended_next_commands[0] if st.recommended_next_commands else ""
            lines.append(
                f"| {st.ticker} | {', '.join(st.warnings)} | {st.d1_latest_market_date or ''} | "
                f"{st.market_qc_stale_days if st.market_qc_stale_days is not None else ''} | `{command}` |"
            )
        if len(warned) > 200:
            lines.append(f"\n_Only first 200 warning tickers shown; see pipeline_state.json for all {len(warned)}._")
    else:
        lines.append("No warning tickers.")

    lines += ["", "## Blocking Tickers", ""]
    if blocked:
        lines.append("| Ticker | Reasons | Next command |")
        lines.append("| --- | --- | --- |")
        for st in blocked[:200]:
            command = st.recommended_next_commands[0] if st.recommended_next_commands else ""
            lines.append(f"| {st.ticker} | {', '.join(st.blocking_reasons)} | `{command}` |")
        if len(blocked) > 200:
            lines.append(f"\n_Only first 200 blocking tickers shown; see pipeline_state.json for all {len(blocked)}._")
    else:
        lines.append("No blocking tickers.")
    out.write_text("\n".join(lines) + "\n")
    return out


def _flag_message(row: dict[str, Any]) -> str:
    stage = str(row.get("stage") or row.get("origin_stage") or "pipeline")
    ticker = str(row.get("ticker") or "").upper()
    statement = row.get("statement_type") or row.get("statement")
    ratio = row.get("canonical_name") if statement == "ratio" else None
    filing = row.get("filing") or row.get("filing_period") or row.get("period_end")
    if row.get("rule") == "source_report_inconsistent_subtotal":
        return str(row.get("message") or f"{ticker} has a source filing subtotal that does not reconcile to visible rows.")
    if stage.startswith("09_qc"):
        return (
            f"{ticker} has an open extraction QC failure"
            f"{f' in {statement}' if statement else ''}"
            f"{f' for {filing}' if filing else ''}. Treat this data slice as unreliable until repaired."
        )
    if stage == "15_ratios":
        return (
            f"{ticker} has a ratio outlier"
            f"{f' for {ratio}' if ratio else ''}"
            f"{f' at {row.get('period_end')}' if row.get('period_end') else ''}. Verify the denominator before relying on it."
        )
    if row.get("rule") == "stale_psx_market_price":
        stale_days = row.get("stale_days")
        return (
            f"{ticker} latest PSX price row is stale"
            f"{f' ({stale_days} days old)' if stale_days is not None else ''}. "
            "Treat price-dependent metrics as stale until PSX publishes a newer row or coverage is excluded."
        )
    if row.get("rule") == "missing_market_data":
        return f"{ticker} is missing PSX market data, so valuation metrics using close or market cap may be unavailable."
    if row.get("rule") == "partial_statement_type_coverage":
        return f"{ticker} has partial statement coverage, so broad financial analysis may omit one or more statement types."
    return str(row.get("message") or f"{ticker} has an open data quality flag from {stage}.")


def _ratio_caution_payload(row: dict[str, Any], message: str) -> dict[str, Any] | None:
    if row.get("stage") != "15_ratios" and row.get("statement_type") != "ratio":
        return None
    ticker = str(row.get("ticker") or "").upper()
    metric = row.get("ratio_name") or row.get("canonical_name") or row.get("metric")
    numerator_name = row.get("numerator_name")
    denominator_name = row.get("denominator_name") or row.get("basis")
    numerator = row.get("numerator")
    denominator = row.get("denominator")
    value = row.get("value")
    if not ticker or not metric:
        return None
    return {
        "ticker": ticker,
        "metric": metric,
        "status": "yellow",
        "short_message": message,
        "basis_notes": [
            "This ratio is computed from deterministic statement rows, but the pipeline detected a unit/scale or denominator anomaly.",
            "Inspect the numerator and denominator before relying on this specific ratio.",
        ],
        "calculation": {
            "basis": "pipeline_ratio_qc",
            "formula": f"{numerator_name or 'numerator'} / {denominator_name or 'denominator'}",
            "numerator": {
                "label": str(numerator_name or "Numerator"),
                "period_end": row.get("period_end"),
                "value": numerator,
            },
            "denominator": {
                "label": str(denominator_name or "Denominator"),
                "period_end": row.get("period_end"),
                "value": denominator,
            },
            "result": value,
        },
        "source": {
            "period_end": row.get("period_end"),
            "period_duration": row.get("period_duration"),
            "section": row.get("section"),
            "failed_stage": "15_ratios",
            "rule": row.get("rule"),
        },
    }


def build_quality_flags(states: list[TickerState]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create serve-consumable, query-scoped quality flags from current ledgers.

    Stage issue ledgers are the source of truth for precise slices. State-only blockers
    add ticker-level flags when there is no narrower source row, e.g. missing market data.
    """
    flags: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_flag(row: dict[str, Any]) -> None:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            return
        stage = str(row.get("stage") or row.get("origin_stage") or "pipeline")
        statement_type = row.get("statement_type") or row.get("statement")
        category = str(row.get("rule") or stage)
        affected_table = "financial_statements"
        if stage == "15_ratios" or statement_type == "ratio":
            affected_table = "ratios"
        elif category in {"missing_market_data", "bad_market_data_values", "stale_psx_market_price"}:
            affected_table = "market_data"
        elif category in {"missing_document_corpus_rows", "fts_document_row_mismatch", "document_rows_missing_jpg_path"}:
            affected_table = "financial_documents_new"

        message = _flag_message(row)
        normalized = {
            "ticker": ticker,
            "severity": row.get("severity", "warning"),
            "status": "open",
            "category": category,
            "affected_table": affected_table,
            "failed_stage": stage,
            "origin_stage": row.get("origin_stage", stage),
            "statement_type": statement_type,
            "filing_period": row.get("filing") or row.get("filing_period"),
            "period_end": row.get("period_end") or row.get("filing_date"),
            "period_duration": row.get("period_duration"),
            "section": row.get("section"),
            "metric": row.get("canonical_name") or row.get("ratio_name"),
            "basis": row.get("basis"),
            "source_file": row.get("source_file") or row.get("file"),
            "source_pages": row.get("source_pages") or row.get("pages"),
            "rule": row.get("rule"),
            "expected": row.get("expected"),
            "got": row.get("got"),
            "value": row.get("value"),
            "stale_days": row.get("stale_days"),
            "message": message,
            "recommended_command": row.get("recommended_command"),
            "user_caution_json": row.get("user_caution_json") or _ratio_caution_payload(row, message),
        }
        identity = stable_id("dqf", {
            k: normalized.get(k)
            for k in (
                "ticker", "affected_table", "statement_type", "filing_period", "period_end",
                "period_duration", "section", "metric", "category", "failed_stage", "source_file",
            )
        })
        if identity in seen:
            return
        seen.add(identity)
        flags.append({"flag_id": identity, **normalized})

    for row in load_stage_issue_rows():
        if (
            row.get("severity") == "blocker"
            or row.get("stage") == "15_ratios"
            or row.get("user_caution_json")
            or row.get("rule") == "source_report_inconsistent_subtotal"
        ):
            add_flag(row)

    for st in states:
        for reason in st.warnings:
            if reason != "stale_psx_market_price":
                continue
            add_flag({
                "ticker": st.ticker,
                "severity": "warning",
                "stage": "16_market_qc",
                "origin_stage": "16_market_qc",
                "rule": reason,
                "period_end": st.d1_latest_market_date,
                "stale_days": st.market_qc_stale_days,
                "message": reason,
                "recommended_command": next((cmd for cmd in st.recommended_next_commands if "16_market" in cmd), None),
            })
        for reason in st.blocking_reasons:
            if reason.startswith("blocking_stage_issues") or reason == "open_extraction_qc_failures":
                continue
            add_flag({
                "ticker": st.ticker,
                "severity": "blocker",
                "stage": "19_pipeline_state",
                "origin_stage": "19_pipeline_state",
                "rule": reason,
                "message": reason,
                "recommended_command": st.recommended_next_commands[0] if st.recommended_next_commands else None,
            })

    flags.sort(key=lambda r: (
        r["ticker"],
        str(r.get("affected_table") or ""),
        str(r.get("statement_type") or ""),
        str(r.get("filing_period") or r.get("period_end") or ""),
        str(r.get("metric") or ""),
    ))
    by_severity = Counter(str(row.get("severity") or "warning") for row in flags)
    by_table = Counter(str(row.get("affected_table") or "unknown") for row in flags)
    by_category = Counter(str(row.get("category") or "unknown") for row in flags)
    summary = {
        "flags": len(flags),
        "flagged_tickers": len({row["ticker"] for row in flags}),
        "by_severity": dict(by_severity),
        "by_table": dict(by_table),
        "by_category": dict(by_category),
    }
    return flags, summary


def apply_quality_flags_to_state(states: list[TickerState], flags: list[dict[str, Any]]) -> dict[str, Any]:
    """Make the state-gate headline match the serve-consumable flag set.

    Step 19's ticker state is built before query-scoped quality flags are emitted.
    Without this pass, the gate can say "12 warning tickers" while the generated
    quality artifact contains statement/ratio warnings for many more tickers.
    """
    by_ticker = _state_by_ticker(states)
    for row in flags:
        ticker = str(row.get("ticker") or "").upper()
        state = by_ticker.get(ticker)
        if not state:
            continue
        category = str(row.get("category") or row.get("rule") or row.get("failed_stage") or "quality_flag")
        severity = str(row.get("severity") or "warning")
        command = row.get("recommended_command")
        if severity == "blocker":
            add_reason(state, category, str(command) if command else None, warning=False)
        else:
            add_reason(state, category, str(command) if command else None, warning=True)

    blocking_counts = Counter(reason for st in states for reason in st.blocking_reasons)
    warning_counts = Counter(reason for st in states for reason in st.warnings)
    return {
        "blocking_tickers": sum(1 for st in states if st.blocking_reasons),
        "warning_tickers": sum(1 for st in states if st.warnings),
        "blocking_reason_counts": dict(blocking_counts),
        "warning_counts": dict(warning_counts),
    }


def _state_by_ticker(states: list[TickerState]) -> dict[str, TickerState]:
    return {state.ticker: state for state in states}


def _statement_short(statement_type: str | None, source_file: str | None = None) -> str | None:
    text = (statement_type or "").lower()
    if text in {"pl", "bs", "cf"}:
        return text
    if "profit" in text or "income" in text:
        return "pl"
    if "balance" in text:
        return "bs"
    if "cash" in text:
        return "cf"
    if source_file:
        parts = source_file.split("_")
        # Current extraction files are ticker_filing_section.md, while issue stage
        # names carry the statement. Keep this as a defensive fallback only.
        for part in parts:
            if part in {"pl", "bs", "cf"}:
                return part
    return None


def build_open_error_queue(states: list[TickerState]) -> list[dict[str, Any]]:
    """Write the operator queue: one row per unresolved blocking data slice."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    def add(row: dict[str, Any]) -> None:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            return
        identity = stable_id("open-error", {
            k: row.get(k)
            for k in (
                "ticker", "failed_stage", "reason", "statement", "filing_period",
                "period_end", "section", "metric", "source_file",
            )
        })
        if identity in seen:
            return
        seen.add(identity)
        rows.append({"error_id": identity, **row})

    for row in load_stage_issue_rows():
        if row.get("severity") != "blocker":
            continue
        ticker = str(row.get("ticker") or "").upper()
        statement = _statement_short(row.get("statement") or row.get("statement_type"), row.get("source_file") or row.get("file"))
        recommended = row.get("recommended_command")
        if not recommended and statement:
            recommended = (
                f"python3 pipeline/08_extract.py --type {statement} --ticker {ticker} "
                "--source pdf --repair-failures --reset && "
                f"python3 pipeline/09_qc.py --type {statement} --ticker {ticker}"
            )
        add({
            "ticker": ticker,
            "status": "open",
            "severity": row.get("severity"),
            "reason": row.get("rule") or row.get("message") or "blocking_stage_issue",
            "origin_stage": row.get("origin_stage") or row.get("stage"),
            "failed_stage": row.get("stage"),
            "re_entry_stage": "08_extract" if str(row.get("stage") or "").startswith("09_qc") else row.get("origin_stage") or row.get("stage"),
            "statement": statement or row.get("statement_type"),
            "statement_type": row.get("statement_type"),
            "filing_period": row.get("filing_period") or row.get("filing"),
            "period_end": row.get("period_end") or row.get("filing_date"),
            "period_duration": row.get("period_duration"),
            "section": row.get("section"),
            "metric": row.get("canonical_name") or row.get("ratio_name"),
            "source_file": row.get("source_file") or row.get("file"),
            "source_pages": row.get("source_pages") or row.get("pages"),
            "expected": row.get("expected"),
            "got": row.get("got"),
            "recommended_command": recommended,
            "verify_command": (
                f"python3 pipeline/09_qc.py --type {statement} --ticker {ticker}"
                if statement in {"pl", "bs", "cf"} else "python3 run.py --chain state"
            ),
        })

    for st in states:
        for reason in st.blocking_reasons:
            if reason in {"open_extraction_qc_failures"} or reason.startswith("blocking_stage_issues"):
                continue
            if reason == "partial_statement_type_coverage":
                missing = [stmt for stmt, count in st.local_flat_rows.items() if count == 0]
                for stmt in missing or [None]:
                    add({
                        "ticker": st.ticker,
                        "status": "open",
                        "severity": "blocker",
                        "reason": reason,
                        "origin_stage": "06_route",
                        "failed_stage": "19_pipeline_state",
                        "re_entry_stage": "06_route" if stmt else "run.py",
                        "statement": stmt,
                        "statement_type": stmt,
                        "filing_period": None,
                        "period_end": st.d1_latest_statement_period,
                        "section": None,
                        "metric": None,
                        "source_file": None,
                        "source_pages": None,
                        "expected": 3,
                        "got": st.d1_statement_types,
                        "recommended_command": (
                            f"python3 pipeline/06_route.py --ticker {st.ticker} && "
                            f"python3 pipeline/08_extract.py --type {stmt} --ticker {st.ticker} --source pdf --reset && "
                            f"python3 pipeline/09_qc.py --type {stmt} --ticker {st.ticker}"
                            if stmt else f"python3 run.py --chain extract --ticker {st.ticker}"
                        ),
                        "verify_command": "python3 run.py --chain state",
                    })
                continue
            add({
                "ticker": st.ticker,
                "status": "open",
                "severity": "blocker",
                "reason": reason,
                "origin_stage": "19_pipeline_state",
                "failed_stage": "19_pipeline_state",
                "re_entry_stage": "run.py",
                "statement": None,
                "statement_type": None,
                "filing_period": None,
                "period_end": st.d1_latest_statement_period,
                "section": None,
                "metric": None,
                "source_file": None,
                "source_pages": None,
                "expected": None,
                "got": None,
                "recommended_command": st.recommended_next_commands[0] if st.recommended_next_commands else None,
                "verify_command": "python3 run.py --chain state",
            })

    rows.sort(key=lambda r: (
        str(r.get("ticker") or ""),
        str(r.get("failed_stage") or ""),
        str(r.get("statement") or ""),
        str(r.get("filing_period") or r.get("period_end") or ""),
        str(r.get("metric") or ""),
    ))
    write_jsonl(OPEN_ERROR_QUEUE, rows)
    return rows


def write_quality_markdown(flags: list[dict[str, Any]], summary: dict[str, Any]) -> Path:
    out = QUALITY_DIR / "data_quality_flags.md"
    lines = [
        "# Data Quality Flags",
        "",
        "Query-scoped flags generated by Step 19. Serve should warn only when retrieved or requested data overlaps the flag scope.",
        "",
        "## Summary",
        "",
        f"- Flags: {summary['flags']}",
        f"- Flagged tickers: {summary['flagged_tickers']}",
        f"- By severity: {summary['by_severity']}",
        f"- By table: {summary['by_table']}",
        "",
        "## Open Flags",
        "",
    ]
    if flags:
        lines.append("| Ticker | Severity | Table | Statement / metric | Period | Section | Stage | Message |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for row in flags[:300]:
            statement = row.get("statement_type") or row.get("metric") or ""
            if row.get("affected_table") == "ratios" and row.get("metric"):
                statement = row["metric"]
            period = row.get("filing_period") or row.get("period_end") or ""
            lines.append(
                f"| {row['ticker']} | {row.get('severity','')} | {row.get('affected_table','')} | "
                f"{statement} | {period} | {row.get('section') or ''} | {row.get('failed_stage','')} | "
                f"{row.get('message','').replace('|', '/')} |"
            )
        if len(flags) > 300:
            lines.append(f"\n_Only first 300 flags shown; see data_quality_flags.jsonl for all {len(flags)}._")
    else:
        lines.append("No open flags.")
    out.write_text("\n".join(lines) + "\n")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic per-ticker pipeline state ledger")
    parser.add_argument("--fail-on-blocking", action="store_true", help="return non-zero if any blocking ticker exists")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    states, summary = build_state()
    open_error_queue = build_open_error_queue(states)
    quality_flags, quality_summary = build_quality_flags(states)
    summary.update(apply_quality_flags_to_state(states, quality_flags))
    summary["quality_flags"] = quality_summary
    state_path = STATE_DIR / "pipeline_state.json"
    state_path.write_text(json.dumps({"summary": summary, "tickers": [asdict(st) for st in states]}, indent=2))
    md_path = write_markdown(states, summary)
    quality_jsonl = write_jsonl(QUALITY_DIR / "data_quality_flags.jsonl", quality_flags)
    quality_json = QUALITY_DIR / "data_quality_flags.json"
    quality_json.write_text(json.dumps({"summary": quality_summary, "flags": quality_flags}, indent=2))
    quality_md = write_quality_markdown(quality_flags, quality_summary)
    write_report("19_pipeline_state", summary | {
        "state_path": str(state_path),
        "markdown_path": str(md_path),
        "open_error_queue_path": str(OPEN_ERROR_QUEUE),
        "open_error_queue_rows": len(open_error_queue),
        "quality_flags": quality_summary,
        "quality_flags_path": str(quality_jsonl),
        "quality_flags_markdown_path": str(quality_md),
    })

    actions: list[Action] = []
    if summary["open_qc_failures"]:
        actions.append(Action("Triage open extraction QC failures", "python3 pipeline/14_triage.py"))
    if summary["blocking_reason_counts"].get("missing_market_data"):
        missing = [st.ticker for st in states if "missing_market_data" in st.blocking_reasons]
        actions.append(Action("Retry missing PSX market rows", f"python3 pipeline/16_market_data.py --tickers {','.join(missing)} --push-d1"))
    if summary["blocking_reason_counts"].get("missing_document_corpus_rows"):
        actions.append(Action("Publish missing document corpus rows", "python3 pipeline/17_publish_documents.py --gap --push-d1"))

    emit_decision(
        "19_pipeline_state",
        f"Pipeline state built for {summary['tickers']:,} tickers: "
        f"{summary['blocking_tickers']:,} blocking, {summary['warning_tickers']:,} warning. "
        f"Open error queue: {len(open_error_queue):,}. Quality flags: {quality_summary['flags']:,}. "
        f"Artifacts: {state_path}, {md_path}, {OPEN_ERROR_QUEUE}, {quality_jsonl}.",
        actions=actions,
    )
    return 1 if args.fail_on_blocking and summary["blocking_tickers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
