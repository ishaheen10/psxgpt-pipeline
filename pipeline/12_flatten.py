#!/usr/bin/env python3
"""Step 12 — Flatten. Per-ticker JSON + derived periods -> data/flat/{type}.jsonl.

PL/CF: quarterly_3M + cumulative_YTD + LTM rows.
BS:    point-in-time only (no derivation), with dedup (prefer primary/annual).

Usage:
    python pipeline/flatten.py --type pl
    python pipeline/flatten.py --type bs --ticker HBL
    python pipeline/flatten.py --type cf
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from shared.report import write_report, emit_decision, write_stage_issues, write_stage_lineage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TICKERS_FILE = PROJECT_ROOT / "tickers100.json"
ROUTE_MANIFEST_FILE = PROJECT_ROOT / "artifacts" / "06_route" / "manifest.json"
LEGACY_STATEMENT_PAGES_FILE = PROJECT_ROOT / "artifacts" / "06_route" / "statement_pages.json"
PDF_BASE_URL = "https://source.psxgpt.com/PDF_PAGES"

# Load once at module level
TICKER_META: dict = {}
if TICKERS_FILE.exists():
    TICKER_META = {t['Symbol']: t for t in json.loads(TICKERS_FILE.read_text())}

STATEMENT_PAGES: dict = {}
if ROUTE_MANIFEST_FILE.exists():
    STATEMENT_PAGES = json.loads(ROUTE_MANIFEST_FILE.read_text())
elif LEGACY_STATEMENT_PAGES_FILE.exists():
    STATEMENT_PAGES = json.loads(LEGACY_STATEMENT_PAGES_FILE.read_text())

BANK_TICKERS = {sym for sym, m in TICKER_META.items() if m.get('Industry') == 'Banking'}

EPS_CANONICAL_MAP = {'eps_basic': 'eps', 'eps_diluted': 'eps', 'eps_basic_diluted': 'eps'}
BANK_REVENUE_REMAP = {'revenue_net': 'total_income'}
SCALE_CHECK_FIELDS = {
    'revenue_net', 'total_income', 'net_premium', 'operating_revenue',
    'gross_profit', 'net_profit', 'operating_profit',
    'total_assets', 'total_equity', 'total_liabilities',
}

NON_NEGATIVE = {
    'pl': {'revenue_net', 'gross_revenue', 'dividend_income', 'interest_income'},
    'cf': {'cash_start', 'cash_end', 'cash_and_equivalents'},
    'bs': {'total_assets', 'total_equity_and_liabilities', 'cash_and_equivalents',
           'property_equipment', 'inventory', 'receivables', 'total_current_assets',
           'total_non_current_assets', 'share_capital'},
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def normalize_value(value: float, unit_type: str, canonical: str = '') -> float | None:
    if value is None:
        return None
    if canonical and ('eps' in canonical.lower() or 'per_share' in canonical.lower()):
        return value
    unit = (unit_type or 'thousands').lower().strip()
    if unit in ('rupees', 'rupee', 'full_rupees') or ('rupee' in unit and 'thousand' in unit):
        return value / 1000.0
    if unit == 'millions':
        return value * 1000.0
    return value  # thousands or unknown


def unit_qc_flag(unit_type: str) -> str:
    unit = (unit_type or 'thousands').lower().strip()
    if unit in {'thousands', 'rupees', 'rupee', 'full_rupees', 'millions'}:
        return ''
    if 'rupee' in unit and 'thousand' in unit:
        return f"ambiguous_unit_type: {unit_type}"
    return f"invalid_unit_type: {unit_type}"


def append_qc_flag(existing: str, new_flag: str) -> str:
    if not new_flag:
        return existing or ''
    if not existing:
        return new_flag
    if new_flag in existing:
        return existing
    return f"{existing}; {new_flag}"


def normalize_canonical_pl(canonical: str, ticker: str) -> str:
    if canonical in EPS_CANONICAL_MAP:
        return EPS_CANONICAL_MAP[canonical]
    if ticker in BANK_TICKERS and canonical in BANK_REVENUE_REMAP:
        return BANK_REVENUE_REMAP[canonical]
    return canonical


def get_row_label_map(period: dict) -> dict:
    return period.get('source_items') or period.get('source_labels') or {}


def get_source_from_md(ticker: str, source: str, page_key: str) -> dict:
    """Extract source_pages + source_url from an MD filename string."""
    if not source or '.md' not in source:
        return {'source_pages': [], 'source_url': ''}
    match = re.search(r'([A-Z0-9]+_(annual|quarterly)_[\d-]+_\w+)\.md', source)
    if not match:
        return {'source_pages': [], 'source_url': ''}
    filename = match.group(1)
    if '_annual_' in filename:
        parts = filename.split('_annual_')
        year = parts[1].split('_')[0]
        period_key = f"annual_{year}"
        folder = f"{ticker}/{year}/{ticker}_Annual_{year}"
    elif '_quarterly_' in filename:
        parts = filename.split('_quarterly_')
        date_part = parts[1].split('_')[0]
        year = date_part[:4]
        period_key = f"quarterly_{date_part}"
        folder = f"{ticker}/{year}/{ticker}_Quarterly_{date_part}"
    else:
        return {'source_pages': [], 'source_url': ''}
    pages = (STATEMENT_PAGES.get(ticker, {}).get(period_key, {})
             .get('consolidated', {}).get(page_key, []))  # fallback; won't match section
    # Actually look up both sections properly
    period_data = STATEMENT_PAGES.get(ticker, {}).get(period_key, {})
    pages = []
    for sec_data in period_data.values():
        if isinstance(sec_data, dict) and page_key in sec_data:
            pages = sec_data[page_key]; break
    return {'source_pages': pages, 'source_url': f"{PDF_BASE_URL}/{folder}"}


def get_qc_flag(ticker: str, period_end: str, section: str, field: str,
                value: float | None, method: str, fiscal_year,
                non_negative: set, allowlist: dict, qc_issues: dict) -> str:
    flag = ''
    if field in non_negative and value is not None and value < 0:
        flag = 'derivation_anomaly' if (method and method not in ('direct_3M', 'direct', '')) else 'unexpected_negative'
    qc_key = (ticker, period_end, section)
    if qc_key in qc_issues and not flag:
        flag = 'qc_flagged'
    note = allowlist.get((ticker, fiscal_year, section), '')
    if flag and note:
        return f"{flag}: {note}"
    if flag:
        return flag
    if note:
        return f"allowlisted: {note}"
    return ''


def _load_allowlist(path: Path) -> dict:
    if not path.exists():
        return {}
    items = json.loads(path.read_text()).get('allowlist', [])
    result = {}
    for item in items:
        key = (item['ticker'], item.get('fiscal_year', item.get('period', '')), item['consolidation'])
        result[key] = item.get('reason', 'Manually reviewed')
    return result


def _coverage_summary(label: str, rows: list[dict], ticker_stats: dict, dur_stats: dict, qc_stats: dict,
                      output: Path, input_counts: dict | None = None) -> dict:
    duration_tickers: dict = defaultdict(set)
    section_tickers: dict = defaultdict(set)
    missing_source_pages = 0
    missing_source_url = 0
    missing_source_file = 0
    ltm_fields: dict = defaultdict(set)

    for row in rows:
        ticker = row.get('ticker', '')
        duration = row.get('period_duration', '')
        section = row.get('section', '')
        duration_tickers[duration].add(ticker)
        section_tickers[section].add(ticker)
        if not row.get('source_pages'):
            missing_source_pages += 1
        if not row.get('source_url'):
            missing_source_url += 1
        if not row.get('source_file'):
            missing_source_file += 1
        if duration == 'LTM':
            ltm_fields[row.get('canonical_name', '')].add(ticker)

    return {
        "type": label,
        "output": str(output.relative_to(PROJECT_ROOT)),
        "rows": len(rows),
        "tickers": len(ticker_stats),
        "input_files": input_counts or {},
        "rows_by_duration": dict(sorted(dur_stats.items())),
        "tickers_by_duration": {k: len(v) for k, v in sorted(duration_tickers.items())},
        "tickers_by_section": {k: len(v) for k, v in sorted(section_tickers.items())},
        "qc_flags": dict(sorted(qc_stats.items(), key=lambda x: (-x[1], x[0]))),
        "missing_source": {
            "rows_missing_source_pages": missing_source_pages,
            "rows_missing_source_url": missing_source_url,
            "rows_missing_source_file": missing_source_file,
        },
        "ltm_key_field_tickers": {
            k: len(ltm_fields.get(k, set()))
            for k in ["net_profit", "revenue_net", "total_income", "gross_profit", "net_cash_from_operating"]
        },
    }


def add_scale_flags(rows: list[dict]) -> None:
    grouped: dict = defaultdict(list)
    for row in rows:
        if row.get('canonical_name') not in SCALE_CHECK_FIELDS:
            continue
        if row.get('period_duration') not in {'3M', '6M', '9M', '12M', 'LTM', 'PIT'}:
            continue
        value = row.get('value')
        if value is None or value == 0:
            continue
        key = (
            row.get('ticker'), row.get('statement_type'), row.get('section'),
            row.get('canonical_name'), row.get('period_duration'),
        )
        grouped[key].append(row)
    for entries in grouped.values():
        entries.sort(key=lambda r: r.get('period_end') or '')
        for prev, cur in zip(entries, entries[1:]):
            a, b = abs(prev.get('value') or 0), abs(cur.get('value') or 0)
            if a < 1_000 or b < 1_000:
                continue
            ratio = max(a, b) / max(min(a, b), 1e-9)
            if ratio >= 100:
                msg = (
                    f"scale_discontinuity: {prev.get('period_end')}={prev.get('value')} "
                    f"to {cur.get('period_end')}={cur.get('value')} ratio={ratio:.1f}"
                )
                cur['qc_flag'] = append_qc_flag(cur.get('qc_flag', ''), msg)
            elif (
                cur.get('period_duration') == 'LTM'
                and 'scale_discontinuity' in (prev.get('qc_flag') or '')
            ):
                msg = (
                    f"scale_discontinuity_carryforward: previous {prev.get('period_end')} "
                    f"row remains within same scale band"
                )
                cur['qc_flag'] = append_qc_flag(cur.get('qc_flag', ''), msg)


def _print_summary(label: str, ticker_stats: dict, all_rows, dur_stats: dict, qc_stats: dict, output: Path) -> None:
    print(f"\n{'='*60}\nFLATTEN COMPLETE [{label.upper()}]\n{'='*60}")
    total = sum(1 for _ in all_rows) if hasattr(all_rows, '__iter__') and not isinstance(all_rows, list) else len(all_rows)
    print(f"Tickers: {len(ticker_stats)}  Rows: {total:,}")
    for d, c in sorted(dur_stats.items()):
        print(f"  {d}: {c:,}")
    if qc_stats:
        print("QC Flags:")
        for flag, count in sorted(qc_stats.items(), key=lambda x: -x[1]):
            print(f"  {flag}: {count:,}")
    print(f"Output: {output}")


def _write_flat_rows(output_file: Path, rows: list[dict], ticker_filter: str | None) -> None:
    merged_rows: list[dict] = []
    scope = ticker_filter.upper() if ticker_filter else None
    if scope and output_file.exists():
        for line in output_file.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("ticker") or "").upper() != scope:
                merged_rows.append(row)
    merged_rows.extend(rows)
    with output_file.open('w') as out:
        for row in merged_rows:
            out.write(json.dumps(row) + "\n")


def _emit_flatten_report(label: str, rows: list[dict], ticker_stats: dict, dur_stats: dict,
                         qc_stats: dict, output: Path, input_counts: dict | None = None,
                         ticker_filter: str | None = None) -> None:
    qc_stats.clear()
    for row in rows:
        if row.get('qc_flag'):
            qc_stats[row['qc_flag'].split(':')[0].strip()] += 1
    summary = _coverage_summary(label, rows, ticker_stats, dur_stats, qc_stats, output, input_counts)
    lineage = [
        {
            "ticker": row.get("ticker"),
            "statement_type": row.get("statement_type"),
            "period_end": row.get("period_end"),
            "period_duration": row.get("period_duration"),
            "section": row.get("section"),
            "canonical_name": row.get("canonical_name"),
            "value": row.get("value"),
            "method": row.get("method"),
            "source_file": row.get("source_file"),
            "source_pages": row.get("source_pages"),
            "source_url": row.get("source_url"),
            "qc_flag": row.get("qc_flag"),
            "origin_stage": "10_normalize" if row.get("method") in ("direct", "direct_3M", "") else "11_derive",
        }
        for row in rows
    ]
    issues = [
        {
            "severity": "warning",
            "ticker": row.get("ticker"),
            "statement_type": row.get("statement_type"),
            "period_end": row.get("period_end"),
            "period_duration": row.get("period_duration"),
            "section": row.get("section"),
            "canonical_name": row.get("canonical_name"),
            "value": row.get("value"),
            "method": row.get("method"),
            "source_file": row.get("source_file"),
            "source_pages": row.get("source_pages"),
            "source_url": row.get("source_url"),
            "qc_flag": row.get("qc_flag"),
            "rule": "flattened_qc_flag",
            "message": row.get("qc_flag"),
            "origin_stage": "11_derive" if row.get("method") not in ("direct", "direct_3M", "") else "10_normalize",
            "recommended_command": f"python3 run.py --chain {label} --ticker {row.get('ticker')} --only flatten_{label}",
        }
        for row in rows
        if row.get("qc_flag")
    ]
    scope_tickers = {ticker_filter.upper()} if ticker_filter else None
    lineage_path = write_stage_lineage(f"12_flatten_{label}", lineage, scope_tickers=scope_tickers)
    issue_path = write_stage_issues(f"12_flatten_{label}", issues, scope_tickers=scope_tickers)
    summary["lineage"] = str(lineage_path.relative_to(PROJECT_ROOT))
    summary["issues"] = str(issue_path.relative_to(PROJECT_ROOT))
    summary["issue_rows"] = len(issues)
    write_report(f"12_flatten_{label}", summary)
    source = summary["missing_source"]
    source_bits = []
    if source["rows_missing_source_pages"]:
        source_bits.append(f"{source['rows_missing_source_pages']:,} rows missing source_pages")
    if source["rows_missing_source_url"]:
        source_bits.append(f"{source['rows_missing_source_url']:,} rows missing source_url")
    if source["rows_missing_source_file"]:
        source_bits.append(f"{source['rows_missing_source_file']:,} rows missing source_file")
    ltm_tickers = summary["tickers_by_duration"].get("LTM", 0)
    ltm_note = f"; {ltm_tickers:,} LTM tickers" if ltm_tickers else ""
    gap_note = (" Source gaps: " + "; ".join(source_bits) + ".") if source_bits else " Source provenance complete."
    emit_decision(
        f"12_flatten_{label}",
        f"[{label}] flattened {summary['rows']:,} rows across {summary['tickers']:,} tickers{ltm_note}."
        f"{gap_note} See artifacts/reports/12_flatten_{label}.json.",
    )


def _get_fiscal_year(period_end: str) -> int:
    try:
        return datetime.strptime(period_end, '%Y-%m-%d').year
    except Exception:
        return int(period_end[:4])


# ---------------------------------------------------------------------------
# PL / CF shared parsers (parametrized by stmt config)
# ---------------------------------------------------------------------------

def _parse_quarterly_flow(filepath: Path, stmt_type: str, page_key: str, normalize_fn,
                           non_negative: set, allowlist: dict, qc_issues: dict) -> list[dict]:
    data = json.loads(filepath.read_text())
    ticker = data['ticker']
    meta = TICKER_META.get(ticker, {})
    company, industry = meta.get("Company Name", ""), meta.get("Industry", "")
    rows = []
    for q in data.get('quarters', []):
        period_end = q['period_end']
        fiscal_year = q['fiscal_year']
        section = q['consolidation']
        method = q.get('method', 'unknown')
        source = q.get('source', '')
        values = q.get('values', {})
        labels = get_row_label_map(q)
        src = get_source_from_md(ticker, source, page_key)
        for field, value in values.items():
            if value is None:
                continue
            field = normalize_fn(field, ticker)
            qc_flag = get_qc_flag(ticker, period_end, section, field, value, method, fiscal_year,
                                   non_negative, allowlist, qc_issues)
            rows.append({"ticker": ticker, "company_name": company, "industry": industry,
                         "unit_type": "thousands", "period_type": "quarterly",
                         "period_end": period_end, "period_duration": "3M",
                         "fiscal_year": fiscal_year, "section": section,
                         "statement_type": stmt_type,
                         "canonical_name": field, "original_name": labels.get(field) or field,
                         "value": value, "method": method,
                         "source_file": source if isinstance(source, str) else str(source),
                         "source_pages": src['source_pages'], "source_url": src['source_url'],
                         "qc_flag": qc_flag})
    return rows


def _parse_json_flow(filepath: Path, stmt_type: str, normalize_fn,
                     non_negative: set, allowlist: dict, qc_issues: dict) -> list[dict]:
    """Parse cumulative (6M/9M/12M) periods from json_pl or json_cf."""
    data = json.loads(filepath.read_text())
    ticker = data['ticker']
    meta = TICKER_META.get(ticker, {})
    company, industry = meta.get("Company Name", ""), meta.get("Industry", "")
    rows = []
    period_peers = defaultdict(list)
    for period in data.get('periods', []):
        if period.get('duration') in ('6M', '9M', '12M'):
            period_peers[(period.get('period_end'), period.get('duration'))].append(period)

    def effective_unit(period: dict, field: str, raw_value: float) -> tuple[str, bool]:
        raw_unit = period.get('raw_unit_type') or period.get('unit_type', 'thousands')
        unit = period.get('unit_type', 'thousands')
        unit_l = (unit or '').lower()
        raw_unit_l = (raw_unit or '').lower()
        if 'thousand' in raw_unit_l and 'million' in raw_unit_l:
            return 'thousands', False
        if unit_l == 'millions':
            for peer in period_peers.get((period.get('period_end'), period.get('duration')), []):
                if peer is period or peer.get('consolidation') == period.get('consolidation'):
                    continue
                peer_unit = (peer.get('unit_type') or '').lower()
                if peer_unit == 'millions':
                    continue
                peer_values = peer.get('values', {})
                peer_raw = peer_values.get(field)
                if isinstance(peer_raw, dict):
                    peer_raw = peer_raw.get('value')
                if not isinstance(peer_raw, (int, float)) or not raw_value:
                    continue
                ratio = abs(raw_value) / max(abs(peer_raw), 1e-9)
                if 0.1 <= ratio <= 10:
                    return 'thousands', True
        if 'rupee' in raw_unit_l and 'thousand' in raw_unit_l:
            return raw_unit, False
        return unit, False

    for period in data.get('periods', []):
        duration = period.get('duration', '')
        if duration not in ('6M', '9M', '12M'):
            continue
        period_end = period['period_end']
        section = period['consolidation']
        unit_type = period.get('unit_type', 'thousands')
        source_file = period.get('source_file') or period.get('source_filing', '')
        source_pages = period.get('source_pages', [])
        source_url = period.get('source_url', '')
        values = period.get('values', {})
        labels = get_row_label_map(period)
        period_type = 'annual' if duration == '12M' else 'quarterly'
        fiscal_year = _get_fiscal_year(period_end)
        for field, value_data in values.items():
            if value_data is None:
                continue
            if isinstance(value_data, dict):
                raw_value = value_data.get('value')
                original_name = value_data.get('source_item') or field
            else:
                raw_value = value_data
                original_name = labels.get(field) or field
            if raw_value is None:
                continue
            field = normalize_fn(field, ticker)
            normalized_unit, suppress_raw_unit_qc = effective_unit(period, field, raw_value)
            value = normalize_value(raw_value, normalized_unit, field)
            qc_flag = get_qc_flag(ticker, period_end, section, field, value, 'direct', fiscal_year,
                                   non_negative, allowlist, qc_issues)
            if not suppress_raw_unit_qc:
                qc_flag = append_qc_flag(qc_flag, unit_qc_flag(period.get('raw_unit_type') or unit_type))
            rows.append({"ticker": ticker, "company_name": company, "industry": industry,
                         "unit_type": "thousands", "period_type": period_type,
                         "period_end": period_end, "period_duration": duration,
                         "fiscal_year": fiscal_year, "section": section,
                         "statement_type": stmt_type,
                         "canonical_name": field, "original_name": original_name,
                         "value": value, "method": "direct",
                         "source_file": source_file, "source_pages": source_pages,
                         "source_url": source_url, "qc_flag": qc_flag})
    return rows


def _parse_ltm_flow(filepath: Path, stmt_type: str, normalize_fn) -> list[dict]:
    data = json.loads(filepath.read_text())
    ticker = data['ticker']
    meta = TICKER_META.get(ticker, {})
    company, industry = meta.get("Company Name", ""), meta.get("Industry", "")
    rows = []
    for period in data.get('ltm_periods', []):
        period_end = period['period_end']
        section = period['consolidation']
        values = period.get('values', {})
        labels = get_row_label_map(period)
        fiscal_year = period.get('fiscal_year')
        method = period.get('method', 'derived')
        qc_flag = period.get('qc_flag', '')
        comp = period.get('source_components', {}).get('current', {})
        source_file = comp.get('source_file')
        source_pages = comp.get('source_pages') or []
        source_url = comp.get('source_url', '')
        for field, value in values.items():
            if value is None:
                continue
            field = normalize_fn(field, ticker)
            rows.append({"ticker": ticker, "company_name": company, "industry": industry,
                         "unit_type": "thousands", "period_type": "quarterly",
                         "period_end": period_end, "period_duration": "LTM",
                         "fiscal_year": fiscal_year, "section": section,
                         "statement_type": stmt_type,
                         "canonical_name": field, "original_name": labels.get(field) or field,
                         "value": value, "method": method,
                         "source_file": source_file, "source_pages": source_pages,
                         "source_url": source_url, "qc_flag": qc_flag})
    return rows


# ---------------------------------------------------------------------------
# BS-specific (point-in-time, with dedup)
# ---------------------------------------------------------------------------

def _is_primary_period(row: dict) -> bool:
    src = row.get('source_file', '')
    end = row.get('period_end', '')
    if '_annual_' in src:
        parts = src.split('_annual_')
        return len(parts) >= 2 and parts[1].split('_')[0] == end[:4]
    if '_quarterly_' in src:
        parts = src.split('_quarterly_')
        return len(parts) >= 2 and parts[1].split('_')[0] == end
    return True


def _parse_json_bs(filepath: Path, non_negative: set, allowlist: dict, qc_issues: dict) -> list[dict]:
    data = json.loads(filepath.read_text())
    ticker = data['ticker']
    meta = TICKER_META.get(ticker, {})
    company, industry = meta.get("Company Name", ""), meta.get("Industry", "")
    fiscal_month = int(meta.get("fiscal_period", "12-31").split('-')[0])
    rows = []
    for period in data.get('periods', []):
        period_end = period['period_end']
        section = period['consolidation']
        filing_type = period.get('filing_type', 'quarterly')
        unit_type = period.get('unit_type', 'thousands')
        source_file = period.get('source_file', '')
        source_pages = period.get('source_pages', [])
        source_url = period.get('source_url', '')
        values = period.get('values', {})
        source_items = period.get('source_items', {})
        period_year = int(period_end[:4])
        period_month = int(period_end[5:7])
        if filing_type == 'annual':
            fiscal_year = period_year
        else:
            fiscal_year = period_year + 1 if period_month > fiscal_month else period_year
        for field, raw_value in values.items():
            if raw_value is None:
                continue
            value = normalize_value(raw_value, unit_type, field)
            qc_flag = get_qc_flag(ticker, period_end, section, field, value, '', fiscal_year,
                                   non_negative, allowlist, qc_issues)
            qc_flag = append_qc_flag(qc_flag, unit_qc_flag(unit_type))
            rows.append({"ticker": ticker, "company_name": company, "industry": industry,
                         "unit_type": "thousands", "period_type": filing_type,
                         "period_end": period_end, "period_duration": "PIT",
                         "fiscal_year": fiscal_year, "section": section,
                         "statement_type": "balance_sheet",
                         "canonical_name": field, "original_name": source_items.get(field, field),
                         "value": value, "method": "",
                         "source_file": source_file, "source_pages": source_pages,
                         "source_url": source_url, "qc_flag": qc_flag})
    return rows


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def _no_normalize(field: str, ticker: str) -> str:
    return field


def run_flow(stmt_key: str, ticker_filter: str | None) -> None:
    """Shared runner for PL and CF."""
    is_pl = (stmt_key == 'pl')
    stmt_type = 'profit_loss' if is_pl else 'cash_flow'
    page_key = 'PL' if is_pl else 'CF'
    normalize_fn = normalize_canonical_pl if is_pl else _no_normalize
    non_negative = NON_NEGATIVE[stmt_key]

    qc_file = (PROJECT_ROOT / "artifacts" / "11_derive" / "qc_pl.json") if is_pl else \
              (PROJECT_ROOT / "artifacts" / "09_qc" / "cf_results.json")
    al_file = (PROJECT_ROOT / "artifacts" / "allowlists" / "pl.json") if is_pl else \
              (PROJECT_ROOT / "artifacts" / "allowlists" / "cf.json")

    # Build QC issue lookup
    qc_issues: dict = {}
    if qc_file.exists():
        qc_data = json.loads(qc_file.read_text())
        if is_pl:
            for issue in qc_data.get('issues', []):
                key = (issue.get('ticker', ''), issue.get('period_end', ''), issue.get('consolidation', 'consolidated'))
                qc_issues[key] = issue.get('issue', 'qc_issue')
        else:
            for result in qc_data.get('results', []):
                if result.get('checks', {}).get('semantic', {}).get('failed', 0) > 0:
                    qc_issues[(result.get('ticker', ''), '', '')] = 'semantic_equation_failure'

    allowlist = _load_allowlist(al_file)
    output_dir = PROJECT_ROOT / "data" / "flat"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{stmt_key}.jsonl"

    quarterly_dir = PROJECT_ROOT / "data" / f"quarterly_{stmt_key}"
    json_dir = PROJECT_ROOT / "data" / f"json_{stmt_key}"
    ltm_dir = PROJECT_ROOT / "data" / f"ltm_{stmt_key}"

    def _files(d: Path) -> list[Path]:
        files = sorted(d.glob("*.json")) if d.exists() else []
        return [f for f in files if f.stem == ticker_filter] if ticker_filter else files

    q_files = _files(quarterly_dir)
    j_files = _files(json_dir)
    l_files = _files(ltm_dir)
    print(f"[{stmt_key.upper()}] {len(q_files)} 3M, {len(j_files)} cumulative, {len(l_files)} LTM tickers")

    all_rows: list = []
    ticker_stats: dict = defaultdict(int)
    dur_stats: dict = defaultdict(int)
    qc_stats: dict = defaultdict(int)

    for f in q_files:
        rows = _parse_quarterly_flow(f, stmt_type, page_key, normalize_fn, non_negative, allowlist, qc_issues)
        all_rows.extend(rows)
    for f in j_files:
        rows = _parse_json_flow(f, stmt_type, normalize_fn, non_negative, allowlist, qc_issues)
        all_rows.extend(rows)
    for f in l_files:
        rows = _parse_ltm_flow(f, stmt_type, normalize_fn)
        all_rows.extend(rows)

    add_scale_flags(all_rows)
    for row in all_rows:
        ticker_stats[row['ticker']] += 1
        dur_stats[row['period_duration']] += 1
        if row.get('qc_flag'):
            qc_stats[row['qc_flag'].split(':')[0].strip()] += 1
    _write_flat_rows(output_file, all_rows, ticker_filter)

    _print_summary(stmt_key, ticker_stats, all_rows, dur_stats, qc_stats, output_file)
    _emit_flatten_report(stmt_key, all_rows, ticker_stats, dur_stats, qc_stats, output_file,
                         {"quarterly": len(q_files), "cumulative": len(j_files), "ltm": len(l_files)},
                         ticker_filter=ticker_filter)


def run_bs(ticker_filter: str | None) -> None:
    qc_file = PROJECT_ROOT / "artifacts" / "09_qc" / "bs_results.json"
    al_file = PROJECT_ROOT / "artifacts" / "allowlists" / "bs.json"
    non_negative = NON_NEGATIVE['bs']

    qc_issues: dict = {}
    if qc_file.exists():
        qc_data = json.loads(qc_file.read_text())
        for ticker, result in qc_data.get('tickers', {}).items():
            for failure in result.get('checks', {}).get('accounting_equation', {}).get('failures', []):
                key = (ticker, failure.get('period_end', ''), failure.get('consolidation', 'consolidated'))
                qc_issues[key] = 'accounting_equation_failure'

    allowlist = _load_allowlist(al_file)
    json_dir = PROJECT_ROOT / "data" / "json_bs"
    output_dir = PROJECT_ROOT / "data" / "flat"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "bs.jsonl"

    files = sorted(json_dir.glob("*.json")) if json_dir.exists() else []
    if ticker_filter:
        files = [f for f in files if f.stem == ticker_filter]
    print(f"[BS] {len(files)} tickers")

    # Collect with dedup: prefer primary period, then prefer annual over quarterly
    all_rows: dict = {}
    for f in files:
        for row in _parse_json_bs(f, non_negative, allowlist, qc_issues):
            key = (row['ticker'], row['period_end'], row['section'], row['canonical_name'])
            if key not in all_rows:
                all_rows[key] = row
            else:
                existing = all_rows[key]
                new_primary = _is_primary_period(row)
                old_primary = _is_primary_period(existing)
                if new_primary and not old_primary:
                    all_rows[key] = row
                elif new_primary == old_primary:
                    if '_annual_' in row.get('source_file', '') and '_annual_' not in existing.get('source_file', ''):
                        all_rows[key] = row

    ticker_stats: dict = defaultdict(int)
    dur_stats: dict = defaultdict(int)
    qc_stats: dict = defaultdict(int)
    rows_list = list(all_rows.values())

    add_scale_flags(rows_list)
    for row in rows_list:
        ticker_stats[row['ticker']] += 1
        dur_stats[row['period_duration']] += 1
        if row.get('qc_flag'):
            qc_stats[row['qc_flag'].split(':')[0].strip()] += 1
    _write_flat_rows(output_file, rows_list, ticker_filter)

    _print_summary('bs', ticker_stats, rows_list, dur_stats, qc_stats, output_file)
    _emit_flatten_report('bs', rows_list, ticker_stats, dur_stats, qc_stats, output_file,
                         {"json": len(files)}, ticker_filter=ticker_filter)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Step 12 — flatten to compiled rows")
    ap.add_argument("--type", required=True, choices=["pl", "bs", "cf"])
    ap.add_argument("--ticker")
    a = ap.parse_args()
    if a.type == 'bs':
        run_bs(a.ticker)
    else:
        run_flow(a.type, a.ticker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
