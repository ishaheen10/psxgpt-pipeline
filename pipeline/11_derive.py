#!/usr/bin/env python3
"""Step 11 — Derive. Quarterly (3M) + LTM periods from reported cumulative JSON.
Balance sheet is point-in-time — no derivation.

Quarters algorithm: Q2=6M-Q1, Q3=9M-6M, Q4=12M-9M (with fallbacks).
LTM formula: current_YTD + prior_12M - prior_comparable_YTD.

Usage:
    python pipeline/derive.py --type pl
    python pipeline/derive.py --type cf --ticker HBL
    python pipeline/derive.py --type bs   # no-op
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TICKERS_FILE = PROJECT_ROOT / "tickers100.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.report import write_report, emit_decision  # noqa: E402


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_value(value: float, unit_type: str, canonical: str | None = None) -> float | None:
    if value is None:
        return None
    if canonical and 'eps' in canonical.lower():
        return value  # EPS always in rupees/share, never rescaled
    unit = (unit_type or 'thousands').lower().strip()
    if unit in ('rupees', 'rupee', 'full_rupees') or ('rupee' in unit and 'thousand' in unit):
        return value / 1000.0
    if unit == 'millions':
        return value * 1000.0
    return value  # thousands or unknown → keep as-is


def _raw_entry_value(entry) -> float | None:
    return entry.get('value') if isinstance(entry, dict) else entry


def build_unit_peer_index(periods: list[dict]) -> dict:
    peers: dict = defaultdict(list)
    for period in periods:
        values = {
            canonical: _raw_entry_value(entry)
            for canonical, entry in (period.get('values') or {}).items()
        }
        peers[(period.get('period_end'), period.get('duration'))].append({
            'consolidation': period.get('consolidation'),
            'unit_type': period.get('unit_type', 'thousands'),
            'raw_unit_type': period.get('raw_unit_type'),
            'values': values,
        })
    return peers


def effective_unit_type(period: dict, canonical: str, raw_value: float | None, peer_index: dict | None = None) -> str:
    unit_type = period.get('unit_type', 'thousands')
    unit_l = (unit_type or '').lower().strip()
    raw_unit_l = (period.get('raw_unit_type') or '').lower()
    if raw_value is None or (canonical and 'eps' in canonical.lower()):
        return unit_type
    if unit_l == 'millions' and 'thousand' in raw_unit_l and 'million' in raw_unit_l:
        return 'thousands'
    if unit_l == 'millions':
        for peer in (peer_index or {}).get((period.get('period_end'), period.get('duration')), []):
            if peer.get('consolidation') == period.get('consolidation'):
                continue
            peer_unit = (peer.get('unit_type') or '').lower().strip()
            if peer_unit == 'millions':
                continue
            peer_raw = peer.get('values', {}).get(canonical)
            if not isinstance(peer_raw, (int, float)) or not raw_value:
                continue
            ratio = abs(raw_value) / max(abs(peer_raw), 1e-9)
            if 0.1 <= ratio <= 10:
                return 'thousands'
        if 'thousand' in raw_unit_l and 'million' in raw_unit_l and abs(raw_value) >= 1_000_000:
            return 'thousands'
    if unit_l in {'thousands', 'thousand'} and 'rupee' in raw_unit_l and 'thousand' in raw_unit_l:
        return 'thousands'
    return unit_type


def normalize_period_values(period: dict, peer_index: dict | None = None) -> dict:
    values = period.get('values', {})
    for canonical, entry in values.items():
        if entry is None:
            continue
        if isinstance(entry, dict):
            raw = entry.get('value')
            if raw is not None:
                entry['value'] = normalize_value(raw, effective_unit_type(period, canonical, raw, peer_index), canonical)
        else:
            values[canonical] = normalize_value(entry, effective_unit_type(period, canonical, entry, peer_index), canonical)
    period['unit_type'] = 'thousands'
    return period


def normalize_period_ltm(period: dict, peer_index: dict | None = None) -> dict:
    values = period.get('values', {})
    normalized = {}
    for canonical, entry in values.items():
        raw = _raw_entry_value(entry)
        normalized[canonical] = normalize_value(raw, effective_unit_type(period, canonical, raw, peer_index), canonical)
    out = dict(period)
    out['unit_type'] = 'thousands'
    out['values'] = normalized
    return out


def load_fiscal_periods() -> dict:
    if not TICKERS_FILE.exists():
        return {}
    tickers = json.loads(TICKERS_FILE.read_text())
    return {t['Symbol']: int(t.get('fiscal_period', '06-30').split('-')[0]) for t in tickers}


def load_arithmetic_allowlist(path: Path) -> set:
    if not path.exists():
        return set()
    data = json.loads(path.read_text())
    return {(item['ticker'], item['fiscal_year'], item['consolidation']) for item in data.get('allowlist', [])}


def get_quarter_end_date(fy_month: int, fy_year: int, quarter: int) -> str:
    start_month = (fy_month % 12) + 1
    q_end_month = ((start_month - 1 + quarter * 3) % 12) or 12
    year = fy_year - 1 if q_end_month > fy_month else fy_year
    day = 31 if q_end_month in (1, 3, 5, 7, 8, 10, 12) else (30 if q_end_month in (4, 6, 9, 11) else 28)
    return f'{year}-{q_end_month:02d}-{day:02d}'


def find_period(periods: list, end_date: str, duration: str) -> dict | None:
    return next((p for p in periods if p['period_end'] == end_date and p['duration'] == duration), None)


def get_anchor_source(period: dict | None) -> str | None:
    return period.get('source_file') if period else None


def get_period_source_labels(period: dict | None) -> dict:
    return (period.get('source_items') or period.get('source_labels') or {}) if period else {}


def get_numeric_value(entry) -> float | None:
    if entry is None:
        return None
    return entry.get('value') if isinstance(entry, dict) else entry


def extract_flat_values(values_dict: dict) -> dict:
    return {k: get_numeric_value(v) for k, v in values_dict.items()}


def derive_quarter_values(base_values: dict, subtract_list: list[dict]) -> dict:
    result = {}
    for key, base_entry in base_values.items():
        base_val = get_numeric_value(base_entry)
        if base_val is None:
            result[key] = None
            continue
        derived = base_val
        for sub in subtract_list:
            sub_val = get_numeric_value(sub.get(key))
            if sub_val is not None:
                derived -= sub_val
            elif key in sub:
                derived = None
                break
        result[key] = derived
    return result


# ---------------------------------------------------------------------------
# PL-specific QC
# ---------------------------------------------------------------------------

INDUSTRY_INCOME_FIELDS = {'Banking': 'net_interest_income', 'Insurance': 'net_premium'}


def load_industries() -> dict:
    if not TICKERS_FILE.exists():
        return {}
    return {t['Symbol']: t.get('Industry', '') for t in json.loads(TICKERS_FILE.read_text())}


def get_income_field(industry: str) -> str:
    return INDUSTRY_INCOME_FIELDS.get(industry, 'revenue_net')


def qc_derived_values_pl(values: dict, method: str, industry: str = '') -> list[str]:
    income_field = get_income_field(industry)
    income = get_numeric_value(values.get(income_field))
    if income is not None and income < 0:
        return [f"Negative {income_field}: {income:,.0f}"]
    return []


def qc_arithmetic_check_pl(quarters: list[dict], annual: dict, industry: str = '', tolerance: float = 0.05) -> list[str]:
    if len(quarters) != 4:
        return []
    q_map = {q['quarter']: q['values'] for q in quarters}
    if not all(f'Q{i}' in q_map for i in range(1, 5)):
        return []
    income_field = get_income_field(industry)
    issues = []
    for field in [income_field, 'gross_profit', 'net_profit']:
        annual_val = get_numeric_value(annual['values'].get(field))
        if annual_val is None:
            continue
        q_vals = [get_numeric_value(q_map[f'Q{i}'].get(field)) for i in range(1, 5)]
        if any(v is None for v in q_vals):
            continue
        q_sum = sum(q_vals)
        pct = abs(q_sum - annual_val) / abs(annual_val) if annual_val != 0 else (float('inf') if q_sum != 0 else 0)
        if pct > tolerance:
            issues.append(f"{field}: Q1+Q2+Q3+Q4={q_sum:,.0f} vs Annual={annual_val:,.0f} (diff={pct*100:.1f}%)")
    return issues


# ---------------------------------------------------------------------------
# CF-specific QC
# ---------------------------------------------------------------------------

def qc_derived_values_cf(values: dict, method: str) -> list[str]:
    cfo, cfi, cff = values.get('cfo'), values.get('cfi'), values.get('cff')
    net = values.get('net_cash_change')
    if all(v is not None for v in (cfo, cfi, cff, net)):
        expected = cfo + cfi + cff
        if abs(net) > 0:
            diff = abs(expected - net) / abs(net)
            if diff > 0.20 and abs(expected - net) > 1000:
                return [f"CFO+CFI+CFF={expected:,.0f} vs net_cash_change={net:,.0f} (diff={diff*100:.1f}%)"]
    return []


def qc_arithmetic_check_cf(quarters: list[dict], annual: dict, tolerance: float = 0.10) -> list[str]:
    if len(quarters) != 4:
        return []
    q_map = {q['quarter']: q['values'] for q in quarters}
    if not all(f'Q{i}' in q_map for i in range(1, 5)):
        return []
    issues = []
    for field in ['cfo', 'cfi', 'cff', 'net_cash_change']:
        annual_val = annual['values'].get(field)
        if annual_val is None:
            continue
        q_vals = [q_map[f'Q{i}'].get(field) for i in range(1, 5)]
        if any(v is None for v in q_vals):
            continue
        q_sum = sum(q_vals)
        if annual_val == 0:
            if q_sum != 0 and abs(q_sum) > 1000:
                issues.append(f"{field}: Q sum={q_sum:,.0f} vs Annual=0")
            continue
        pct = abs(q_sum - annual_val) / abs(annual_val)
        if pct > tolerance:
            issues.append(f"{field}: Q1+Q2+Q3+Q4={q_sum:,.0f} vs Annual={annual_val:,.0f} (diff={pct*100:.1f}%)")
    return issues


# ---------------------------------------------------------------------------
# Shared quarters derivation (parametrized by per-type QC callbacks)
# ---------------------------------------------------------------------------

def _make_quarter(quarter: str, period_end: str, fy_year: int, cons_type: str, method: str,
                  source_period: dict | None, values: dict, source_labels: dict) -> dict:
    return {'quarter': quarter, 'period_end': period_end, 'fiscal_year': fy_year,
            'consolidation': cons_type, 'method': method,
            'source': get_anchor_source(source_period), 'values': values,
            'source_labels': source_labels}


def process_quarters_ticker(ticker: str, data: dict, fy_month: int,
                            qc_derived_fn: Callable, qc_arith_fn: Callable,
                            industry: str = '') -> tuple[list, list]:
    derived_quarters: list = []
    qc_issues: list = []

    for cons_type in ('consolidated', 'unconsolidated'):
        cons_periods = [p for p in data['periods'] if p.get('consolidation') == cons_type]
        if not cons_periods:
            continue

        source_labels: dict = {}
        for p in cons_periods:
            labels = get_period_source_labels(p)
            if labels:
                source_labels = labels
                break

        annuals = [p for p in cons_periods if p['duration'] == '12M']

        for annual in annuals:
            fy_end = annual['period_end']
            fy_year = int(fy_end[:4])
            if int(fy_end[5:7]) != fy_month:
                continue

            q1_end = get_quarter_end_date(fy_month, fy_year, 1)
            q2_end = get_quarter_end_date(fy_month, fy_year, 2)
            q3_end = get_quarter_end_date(fy_month, fy_year, 3)
            q4_end = fy_end

            p_3m_q1 = find_period(cons_periods, q1_end, '3M')
            p_3m_q2 = find_period(cons_periods, q2_end, '3M')
            p_3m_q3 = find_period(cons_periods, q3_end, '3M')
            p_3m_q4 = find_period(cons_periods, q4_end, '3M')
            p_6m = find_period(cons_periods, q2_end, '6M')
            p_9m = find_period(cons_periods, q3_end, '9M')
            p_12m = annual
            fy_quarters: list = []

            # Q1
            q1_result = None
            if p_3m_q1:
                q1_result = _make_quarter('Q1', q1_end, fy_year, cons_type, 'direct_3M',
                                          p_3m_q1, extract_flat_values(p_3m_q1['values']), source_labels)
                fy_quarters.append(q1_result)

            # Q2
            q2_result = None
            if p_3m_q2:
                q2_result = _make_quarter('Q2', q2_end, fy_year, cons_type, 'direct_3M',
                                          p_3m_q2, extract_flat_values(p_3m_q2['values']), source_labels)
                fy_quarters.append(q2_result)
            elif p_6m and q1_result:
                dv = derive_quarter_values(p_6m['values'], [q1_result['values']])
                q2_result = _make_quarter('Q2', q2_end, fy_year, cons_type, '6M-Q1', p_6m, dv, source_labels)
                issues = qc_derived_fn(dv, '6M-Q1')
                if issues:
                    qc_issues.append({'ticker': ticker, 'quarter': 'Q2', 'fiscal_year': fy_year,
                                      'consolidation': cons_type, 'method': '6M-Q1', 'issues': issues})
                fy_quarters.append(q2_result)
            elif p_9m and q1_result and p_3m_q3:
                dv = derive_quarter_values(p_9m['values'], [q1_result['values'], p_3m_q3['values']])
                q2_result = _make_quarter('Q2', q2_end, fy_year, cons_type, '9M-Q1-Q3', p_9m, dv, source_labels)
                issues = qc_derived_fn(dv, '9M-Q1-Q3')
                if issues:
                    qc_issues.append({'ticker': ticker, 'quarter': 'Q2', 'fiscal_year': fy_year,
                                      'consolidation': cons_type, 'method': '9M-Q1-Q3', 'issues': issues})
                fy_quarters.append(q2_result)

            # Q3
            q3_result = None
            if p_3m_q3:
                q3_result = _make_quarter('Q3', q3_end, fy_year, cons_type, 'direct_3M',
                                          p_3m_q3, extract_flat_values(p_3m_q3['values']), source_labels)
                fy_quarters.append(q3_result)
            elif p_9m and p_6m:
                dv = derive_quarter_values(p_9m['values'], [p_6m['values']])
                q3_result = _make_quarter('Q3', q3_end, fy_year, cons_type, '9M-6M', p_9m, dv, source_labels)
                issues = qc_derived_fn(dv, '9M-6M')
                if issues:
                    qc_issues.append({'ticker': ticker, 'quarter': 'Q3', 'fiscal_year': fy_year,
                                      'consolidation': cons_type, 'method': '9M-6M', 'issues': issues})
                fy_quarters.append(q3_result)
            elif p_9m and q1_result and q2_result:
                dv = derive_quarter_values(p_9m['values'], [q1_result['values'], q2_result['values']])
                q3_result = _make_quarter('Q3', q3_end, fy_year, cons_type, '9M-Q1-Q2', p_9m, dv, source_labels)
                issues = qc_derived_fn(dv, '9M-Q1-Q2')
                if issues:
                    qc_issues.append({'ticker': ticker, 'quarter': 'Q3', 'fiscal_year': fy_year,
                                      'consolidation': cons_type, 'method': '9M-Q1-Q2', 'issues': issues})
                fy_quarters.append(q3_result)

            # Q4
            q4_result = None
            if p_3m_q4:
                q4_result = _make_quarter('Q4', q4_end, fy_year, cons_type, 'direct_3M',
                                          p_3m_q4, extract_flat_values(p_3m_q4['values']), source_labels)
                fy_quarters.append(q4_result)
            elif p_9m:
                dv = derive_quarter_values(p_12m['values'], [p_9m['values']])
                q4_result = _make_quarter('Q4', q4_end, fy_year, cons_type, '12M-9M', p_12m, dv, source_labels)
                issues = qc_derived_fn(dv, '12M-9M')
                if issues:
                    qc_issues.append({'ticker': ticker, 'quarter': 'Q4', 'fiscal_year': fy_year,
                                      'consolidation': cons_type, 'method': '12M-9M', 'issues': issues})
                fy_quarters.append(q4_result)
            elif q1_result and q2_result and q3_result:
                dv = derive_quarter_values(p_12m['values'], [q1_result['values'], q2_result['values'], q3_result['values']])
                q4_result = _make_quarter('Q4', q4_end, fy_year, cons_type, '12M-Q1-Q2-Q3', p_12m, dv, source_labels)
                issues = qc_derived_fn(dv, '12M-Q1-Q2-Q3')
                if issues:
                    qc_issues.append({'ticker': ticker, 'quarter': 'Q4', 'fiscal_year': fy_year,
                                      'consolidation': cons_type, 'method': '12M-Q1-Q2-Q3', 'issues': issues})
                fy_quarters.append(q4_result)
            elif p_6m and q3_result:
                dv = derive_quarter_values(p_12m['values'], [p_6m['values'], q3_result['values']])
                q4_result = _make_quarter('Q4', q4_end, fy_year, cons_type, '12M-6M-Q3', p_12m, dv, source_labels)
                issues = qc_derived_fn(dv, '12M-6M-Q3')
                if issues:
                    qc_issues.append({'ticker': ticker, 'quarter': 'Q4', 'fiscal_year': fy_year,
                                      'consolidation': cons_type, 'method': '12M-6M-Q3', 'issues': issues})
                fy_quarters.append(q4_result)

            arith_issues = qc_arith_fn(fy_quarters, annual)
            if arith_issues:
                qc_issues.append({'ticker': ticker, 'quarter': 'FY', 'fiscal_year': fy_year,
                                   'consolidation': cons_type, 'method': 'arithmetic_check', 'issues': arith_issues})
            derived_quarters.extend(fy_quarters)

        # Orphan 3M periods (current FY without annual yet)
        covered = {q['period_end'] for q in derived_quarters if q['consolidation'] == cons_type}
        for orphan in [p for p in cons_periods if p['duration'] == '3M' and p['period_end'] not in covered]:
            period_end = orphan['period_end']
            period_month = int(period_end[5:7])
            period_year = int(period_end[:4])
            months_after_fy = (period_month - fy_month) % 12 or 12
            quarter_num = (months_after_fy + 2) // 3
            fiscal_year = period_year + 1 if period_month > fy_month else period_year
            derived_quarters.append(_make_quarter(f'Q{quarter_num}', period_end, fiscal_year, cons_type,
                                                  'direct_3M', orphan, extract_flat_values(orphan['values']), source_labels))

        # Orphan 9M → derive Q3
        covered = {q['period_end'] for q in derived_quarters if q['consolidation'] == cons_type}
        for orphan in [p for p in cons_periods if p['duration'] == '9M' and p['period_end'] not in covered]:
            period_end = orphan['period_end']
            period_month = int(period_end[5:7])
            period_year = int(period_end[:4])
            months_after_fy = (period_month - fy_month) % 12 or 12
            if (months_after_fy + 2) // 3 != 3:
                continue
            fiscal_year = period_year + 1 if period_month > fy_month else period_year
            q1_end = get_quarter_end_date(fy_month, fiscal_year, 1)
            q2_end = get_quarter_end_date(fy_month, fiscal_year, 2)
            p_6m = find_period(cons_periods, q2_end, '6M')
            p_q1 = find_period(cons_periods, q1_end, '3M')
            p_q2 = find_period(cons_periods, q2_end, '3M')
            if p_6m:
                dv, method = derive_quarter_values(orphan['values'], [p_6m['values']]), '9M-6M'
            elif p_q1 and p_q2:
                dv, method = derive_quarter_values(orphan['values'], [p_q1['values'], p_q2['values']]), '9M-Q1-Q2'
            else:
                continue
            issues = qc_derived_fn(dv, method)
            if issues:
                qc_issues.append({'ticker': ticker, 'quarter': 'Q3', 'fiscal_year': fiscal_year,
                                   'consolidation': cons_type, 'method': method, 'issues': issues})
            derived_quarters.append(_make_quarter('Q3', period_end, fiscal_year, cons_type, method, orphan, dv, source_labels))

        # Orphan 6M → derive Q2
        covered = {q['period_end'] for q in derived_quarters if q['consolidation'] == cons_type}
        for orphan in [p for p in cons_periods if p['duration'] == '6M' and p['period_end'] not in covered]:
            period_end = orphan['period_end']
            period_month = int(period_end[5:7])
            period_year = int(period_end[:4])
            months_after_fy = (period_month - fy_month) % 12 or 12
            if (months_after_fy + 2) // 3 != 2:
                continue
            fiscal_year = period_year + 1 if period_month > fy_month else period_year
            q1_end = get_quarter_end_date(fy_month, fiscal_year, 1)
            p_q1 = find_period(cons_periods, q1_end, '3M')
            if not p_q1:
                continue
            dv = derive_quarter_values(orphan['values'], [p_q1['values']])
            issues = qc_derived_fn(dv, '6M-Q1')
            if issues:
                qc_issues.append({'ticker': ticker, 'quarter': 'Q2', 'fiscal_year': fiscal_year,
                                   'consolidation': cons_type, 'method': '6M-Q1', 'issues': issues})
            derived_quarters.append(_make_quarter('Q2', period_end, fiscal_year, cons_type, '6M-Q1', orphan, dv, source_labels))

    return derived_quarters, qc_issues


# ---------------------------------------------------------------------------
# LTM (shared core, minor CF-specific dedup logic)
# ---------------------------------------------------------------------------

def _parse_date_ltm(date_str: str) -> datetime:
    return datetime.strptime(date_str, '%Y-%m-%d')


def _previous_year_same_date(date_str: str) -> str:
    dt = _parse_date_ltm(date_str)
    try:
        return dt.replace(year=dt.year - 1).strftime('%Y-%m-%d')
    except ValueError:
        return dt.replace(year=dt.year - 1, day=28).strftime('%Y-%m-%d')


def _build_ltm_indexes(periods: list[dict]) -> tuple[dict, dict]:
    by_key: dict = {}
    annuals_by_scope: dict = {}
    for p in periods:
        key = (p.get('consolidation'), p.get('period_end'), p.get('duration'))
        by_key[key] = p
        if p.get('duration') == '12M':
            annuals_by_scope.setdefault(p.get('consolidation'), []).append(p)
    for scope_periods in annuals_by_scope.values():
        scope_periods.sort(key=lambda p: p['period_end'])
    return by_key, annuals_by_scope


def _latest_annual_before(annuals: list[dict], current_end: str) -> dict | None:
    candidate = None
    for p in annuals:
        if p['period_end'] < current_end:
            candidate = p
        else:
            break
    return candidate


def _has_direct_provenance(period: dict) -> bool:
    return bool(period.get('source_file')) and bool(period.get('source_pages')) and bool(period.get('source_url'))


def _derive_ltm_period(current: dict, prior_annual: dict, prior_same: dict) -> tuple[dict | None, list[str]]:
    if not all(_has_direct_provenance(p) for p in (current, prior_annual, prior_same)):
        return None, ['missing_direct_provenance_component']
    cv, av, pv = current.get('values', {}), prior_annual.get('values', {}), prior_same.get('values', {})
    common = set(cv) & set(av) & set(pv)
    derived = {f: cv[f] + av[f] - pv[f] for f in sorted(common)
               if cv.get(f) is not None and av.get(f) is not None and pv.get(f) is not None}
    if not derived:
        return None, ['no_common_numeric_fields']
    duration = current['duration']
    return {
        'period_end': current['period_end'], 'duration': 'LTM', 'base_duration': duration,
        'fiscal_year': current.get('fiscal_year'), 'consolidation': current.get('consolidation'),
        'unit_type': 'thousands', 'method': f"LTM_{duration}+prior12M-prior{duration}",
        'values': derived,
        'source_labels': current.get('source_items') or current.get('source_labels') or {},
        'source_components': {
            'current': {k: current.get(k) for k in ('period_end', 'duration', 'source_file', 'source_pages', 'source_url')},
            'prior_annual': {k: prior_annual.get(k) for k in ('period_end', 'duration', 'source_file', 'source_pages', 'source_url')},
            'prior_same_duration': {k: prior_same.get(k) for k in ('period_end', 'duration', 'source_file', 'source_pages', 'source_url')},
        },
        'qc_flag': '',
    }, []


def _dedupe_ltm_periods(periods: list[dict]) -> tuple[list[dict], list[dict]]:
    """CF-specific: prefer longer base_duration when multiple LTM periods share the same period_end."""
    grouped: dict = {}
    for p in periods:
        grouped.setdefault((p.get('consolidation'), p.get('period_end')), []).append(p)
    selected, duplicates = [], []
    dur_priority = {'9M': 3, '6M': 2, '3M': 1}
    for key, candidates in grouped.items():
        if len(candidates) == 1:
            selected.append(candidates[0])
            continue
        winner = max(candidates, key=lambda p: dur_priority.get(p.get('base_duration'), 0))
        selected.append(winner)
        duplicates.append({'consolidation': key[0], 'period_end': key[1],
                           'selected_base_duration': winner.get('base_duration'),
                           'discarded': [{'base_duration': p.get('base_duration')} for p in candidates if p is not winner]})
    selected.sort(key=lambda p: (p['consolidation'], p['period_end']))
    return selected, duplicates


def _process_ltm_ticker(path: Path, use_dedup: bool = False) -> tuple[dict, dict]:
    raw = json.loads(path.read_text())
    ticker = raw['ticker']
    raw_periods = raw.get('periods', [])
    peer_index = build_unit_peer_index(raw_periods)
    periods = [normalize_period_ltm(p, peer_index) for p in raw_periods]
    by_key, annuals_by_scope = _build_ltm_indexes(periods)
    ltm_periods, qc_issues = [], []
    counters = {'candidates': 0, 'generated': 0,
                'missing_prior_annual': 0, 'missing_prior_same_duration': 0, 'qc_failures': 0}

    for current in periods:
        duration = current.get('duration')
        if duration not in ('3M', '6M', '9M'):
            continue
        counters['candidates'] += 1
        scope = current.get('consolidation')
        current_end = current.get('period_end')
        prior_annual = _latest_annual_before(annuals_by_scope.get(scope, []), current_end)
        if not prior_annual:
            counters['missing_prior_annual'] += 1
            qc_issues.append({'ticker': ticker, 'period_end': current_end, 'consolidation': scope,
                               'base_duration': duration, 'issue': 'missing_prior_annual'})
            continue
        prior_same_end = _previous_year_same_date(current_end)
        prior_same = by_key.get((scope, prior_same_end, duration))
        if not prior_same:
            counters['missing_prior_same_duration'] += 1
            qc_issues.append({'ticker': ticker, 'period_end': current_end, 'consolidation': scope,
                               'base_duration': duration, 'issue': 'missing_prior_same_duration',
                               'expected_period_end': prior_same_end})
            continue
        ltm_period, issues = _derive_ltm_period(current, prior_annual, prior_same)
        if issues:
            counters['qc_failures'] += 1
            qc_issues.append({'ticker': ticker, 'period_end': current_end, 'consolidation': scope,
                               'base_duration': duration, 'issue': 'derive_ltm_failed', 'details': issues})
            continue
        ltm_periods.append(ltm_period)
        counters['generated'] += 1

    duplicate_candidates: list = []
    if use_dedup:
        ltm_periods, duplicate_candidates = _dedupe_ltm_periods(ltm_periods)
        counters['deduped_candidates'] = sum(len(d['discarded']) for d in duplicate_candidates)

    output = {'ticker': ticker, 'generated_at': utc_now_iso(),
              'ltm_periods': sorted(ltm_periods, key=lambda p: (p['consolidation'], p['period_end']))}
    qc = {'ticker': ticker, 'summary': counters, 'issues': qc_issues,
          'duplicate_candidates': duplicate_candidates}
    return output, qc


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def _run_quarters(stmt_type: str, input_dir: Path, output_dir: Path, qc_output: Path,
                  allowlist_path: Path, ticker_filter: str | None,
                  qc_derived_fn: Callable, qc_arith_fn: Callable,
                  load_industry: bool = False) -> None:
    if not input_dir.exists():
        print(f"ERROR: {input_dir} not found"); return
    output_dir.mkdir(parents=True, exist_ok=True)
    qc_output.parent.mkdir(parents=True, exist_ok=True)
    fiscal_periods = load_fiscal_periods()
    industries = load_industries() if load_industry else {}
    allowlist = load_arithmetic_allowlist(allowlist_path)
    files = sorted(input_dir.glob('*.json'))
    if ticker_filter:
        files = [f for f in files if f.stem == ticker_filter]
    print(f"[{stmt_type}] quarters: {len(files)} tickers")
    all_qc, stats = [], {'tickers': 0, 'total': 0, 'direct': 0, 'derived': 0, 'issues': 0, 'by_method': defaultdict(int)}
    for jf in files:
        ticker = jf.stem
        fy_month = fiscal_periods.get(ticker, 6)
        industry = industries.get(ticker, '')
        data = json.loads(jf.read_text())
        peer_index = build_unit_peer_index(data.get('periods', []))
        for p in data.get('periods', []):
            normalize_period_values(p, peer_index)
        quarters, issues = process_quarters_ticker(ticker, data, fy_month, qc_derived_fn, qc_arith_fn, industry)
        stats['tickers'] += 1; stats['total'] += len(quarters); stats['issues'] += len(issues)
        for q in quarters:
            m = q['method']; stats['by_method'][m] += 1
            stats['direct' if m == 'direct_3M' else 'derived'] += 1
        all_qc.extend(issues)
        filtered_issues = [i for i in issues if (ticker, i.get('fiscal_year', 0), i.get('consolidation', '')) not in allowlist]
        (output_dir / jf.name).write_text(json.dumps({'ticker': ticker, 'fiscal_year_end_month': fy_month, 'quarters': quarters}, indent=2))
        suffix = f" ({len(filtered_issues)} QC issues)" if filtered_issues else ""
        print(f"  {ticker}: {len(quarters)} quarters{suffix}")
    qc_output.write_text(json.dumps({'generated_at': utc_now_iso(), 'issues': all_qc,
                                      'summary': {**stats, 'by_method': dict(stats['by_method'])}}, indent=2))
    print(f"\n[{stmt_type}] quarters done: {stats['total']} quarters from {stats['tickers']} tickers "
          f"({stats['direct']} direct, {stats['derived']} derived, {stats['issues']} QC issues)")


def _run_ltm(stmt_type: str, input_dir: Path, output_dir: Path, qc_output: Path,
             ticker_filter: str | None, use_dedup: bool = False) -> None:
    if not input_dir.exists():
        print(f"ERROR: {input_dir} not found"); return
    output_dir.mkdir(parents=True, exist_ok=True)
    qc_output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(input_dir.glob('*.json'))
    if ticker_filter:
        files = [f for f in files if f.stem == ticker_filter]
    print(f"[{stmt_type}] ltm: {len(files)} tickers")
    qc_results, total = [], 0
    for path in files:
        output, qc = _process_ltm_ticker(path, use_dedup=use_dedup)
        (output_dir / path.name).write_text(json.dumps(output, indent=2))
        qc_results.append(qc); total += qc['summary']['generated']
    aggregate = _summarize_ltm_qc(qc_results, total)
    qc_output.write_text(json.dumps({'generated_at': utc_now_iso(), 'results': qc_results,
                                      'summary': aggregate}, indent=2))
    write_report(f"11_derive_ltm_{stmt_type}", {
        "type": stmt_type,
        "input_tickers": len(qc_results),
        "output_dir": str(output_dir.relative_to(PROJECT_ROOT)),
        "qc_output": str(qc_output.relative_to(PROJECT_ROOT)),
        **aggregate,
    })
    emit_decision(
        f"11_derive_ltm_{stmt_type}",
        _format_ltm_decision(stmt_type, aggregate),
    )
    print(f"[{stmt_type}] ltm done: {total} LTM periods from {len(qc_results)} tickers")


def _summarize_ltm_qc(qc_results: list[dict], total_generated: int) -> dict:
    issue_counts: dict = defaultdict(int)
    zero_generated: list[dict] = []
    generated_tickers = 0
    candidate_tickers = 0
    totals = {
        'candidates': 0,
        'generated': total_generated,
        'missing_prior_annual': 0,
        'missing_prior_same_duration': 0,
        'qc_failures': 0,
    }

    for rec in qc_results:
        summary = rec.get('summary', {})
        candidates = int(summary.get('candidates') or 0)
        generated = int(summary.get('generated') or 0)
        if candidates:
            candidate_tickers += 1
        if generated:
            generated_tickers += 1
        for key in ('candidates', 'missing_prior_annual', 'missing_prior_same_duration', 'qc_failures'):
            totals[key] += int(summary.get(key) or 0)
        for issue in rec.get('issues', []):
            issue_counts[issue.get('issue', 'unknown')] += 1
        if candidates and not generated:
            reasons: dict = defaultdict(int)
            for issue in rec.get('issues', []):
                reasons[issue.get('issue', 'unknown')] += 1
            zero_generated.append({
                'ticker': rec.get('ticker'),
                'candidates': candidates,
                'reasons': dict(sorted(reasons.items())),
            })

    return {
        'tickers': len(qc_results),
        'candidate_tickers': candidate_tickers,
        'generated_tickers': generated_tickers,
        'zero_generated_tickers': len(zero_generated),
        'generated_ltm_periods': total_generated,
        'candidates': totals['candidates'],
        'missing_prior_annual': totals['missing_prior_annual'],
        'missing_prior_same_duration': totals['missing_prior_same_duration'],
        'qc_failures': totals['qc_failures'],
        'issue_counts': dict(sorted(issue_counts.items(), key=lambda x: (-x[1], x[0]))),
        'zero_generated_examples': zero_generated[:50],
    }


def _format_ltm_decision(stmt_type: str, summary: dict) -> str:
    parts = [
        f"[{stmt_type}] LTM derived {summary['generated_ltm_periods']:,} periods "
        f"for {summary['generated_tickers']:,}/{summary['candidate_tickers']:,} candidate tickers.",
    ]
    gaps = []
    if summary.get('zero_generated_tickers'):
        gaps.append(f"{summary['zero_generated_tickers']:,} tickers generated no LTM")
    if summary.get('missing_prior_annual'):
        gaps.append(f"{summary['missing_prior_annual']:,} candidates missing prior annual")
    if summary.get('missing_prior_same_duration'):
        gaps.append(f"{summary['missing_prior_same_duration']:,} candidates missing prior same-duration period")
    if summary.get('qc_failures'):
        gaps.append(f"{summary['qc_failures']:,} candidates failed LTM QC/provenance")
    if gaps:
        parts.append("Coverage gaps: " + "; ".join(gaps) + ".")
    if summary.get('zero_generated_examples'):
        examples = ", ".join(item['ticker'] for item in summary['zero_generated_examples'][:12])
        parts.append(f"Review zero-LTM examples in artifacts/11_derive/ltm_{stmt_type}_qc.json: {examples}.")
    return " ".join(parts)


def run_pl(ticker_filter: str | None) -> None:
    qc_derived = lambda v, m: qc_derived_values_pl(v, m)  # industry resolved inside ticker loop
    _run_quarters('pl',
                  input_dir=PROJECT_ROOT / 'data' / 'json_pl',
                  output_dir=PROJECT_ROOT / 'data' / 'quarterly_pl',
                  qc_output=PROJECT_ROOT / 'artifacts' / '11_derive' / 'qc_pl.json',
                  allowlist_path=PROJECT_ROOT / 'artifacts' / 'allowlists' / 'pl.json',
                  ticker_filter=ticker_filter,
                  qc_derived_fn=lambda v, m: qc_derived_values_pl(v, m),
                  qc_arith_fn=lambda q, a: qc_arithmetic_check_pl(q, a),
                  load_industry=True)
    _run_ltm('pl',
             input_dir=PROJECT_ROOT / 'data' / 'json_pl',
             output_dir=PROJECT_ROOT / 'data' / 'ltm_pl',
             qc_output=PROJECT_ROOT / 'artifacts' / '11_derive' / 'ltm_pl_qc.json',
             ticker_filter=ticker_filter, use_dedup=False)


def run_cf(ticker_filter: str | None) -> None:
    _run_quarters('cf',
                  input_dir=PROJECT_ROOT / 'data' / 'json_cf',
                  output_dir=PROJECT_ROOT / 'data' / 'quarterly_cf',
                  qc_output=PROJECT_ROOT / 'artifacts' / '11_derive' / 'qc_cf.json',
                  allowlist_path=PROJECT_ROOT / 'artifacts' / 'allowlists' / 'cf.json',
                  ticker_filter=ticker_filter,
                  qc_derived_fn=qc_derived_values_cf,
                  qc_arith_fn=qc_arithmetic_check_cf,
                  load_industry=False)
    _run_ltm('cf',
             input_dir=PROJECT_ROOT / 'data' / 'json_cf',
             output_dir=PROJECT_ROOT / 'data' / 'ltm_cf',
             qc_output=PROJECT_ROOT / 'artifacts' / '11_derive' / 'ltm_cf_qc.json',
             ticker_filter=ticker_filter, use_dedup=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Step 11 — derive quarterly + LTM periods")
    ap.add_argument("--type", required=True, choices=["pl", "cf", "bs"])
    ap.add_argument("--ticker")
    a = ap.parse_args()
    if a.type == "bs":
        print("[bs] balance sheet is point-in-time — no derivation."); return 0
    {"pl": run_pl, "cf": run_cf}[a.type](a.ticker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
