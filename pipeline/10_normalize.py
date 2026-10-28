#!/usr/bin/env python3
"""Step 10 — Normalize. Extracted Ref-column markdown -> per-ticker JSON keyed by canonical name.

PL and CF use period-column headers ("12M Dec 2023"); BS uses date-column headers ("30 Jun 2024").
All three write to data/json_{type}/{TICKER}.json.

Usage:
    python pipeline/normalize.py --type pl
    python pipeline/normalize.py --type bs --ticker HBL
    python pipeline/normalize.py --type cf --verbose
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from shared.report import emit_decision, write_report, write_stage_issues, write_stage_lineage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PDF_BASE_URL = "https://source.psxgpt.com/PDF_PAGES"
ROUTE_MANIFEST_FILE = PROJECT_ROOT / "artifacts" / "06_route" / "manifest.json"
LEGACY_STATEMENT_PAGES_FILE = PROJECT_ROOT / "artifacts" / "06_route" / "statement_pages.json"

STATEMENT_PAGES: dict = {}
if ROUTE_MANIFEST_FILE.exists():
    STATEMENT_PAGES = json.loads(ROUTE_MANIFEST_FILE.read_text())
elif LEGACY_STATEMENT_PAGES_FILE.exists():
    STATEMENT_PAGES = json.loads(LEGACY_STATEMENT_PAGES_FILE.read_text())

MONTH_MAP = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'september': 9, 'oct': 10, 'october': 10,
    'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}
MONTH_DAYS = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

# PL-specific: fields that are authoritative even without a Ref formula
PL_OVERWRITE_FIELDS = {
    'profit_before_tax', 'taxation', 'net_profit', 'gross_profit',
    'operating_profit', 'net_profit_parent', 'net_profit_continuing',
}
NORMALIZE_LINEAGE: list[dict] = []
NORMALIZE_ISSUES: list[dict] = []
TYPE_COUNTS: dict[str, dict[str, int]] = defaultdict(lambda: {"tickers": 0, "periods": 0})
VALID_UNITS = {"thousands", "rupees", "rupee", "full_rupees", "millions"}
UNIT_ALIASES = {
    "pkr": "rupees",
    "rs": "rupees",
    "rupee": "rupees",
    "rupees": "rupees",
    "full rupees": "full_rupees",
    "full_rupees": "full_rupees",
    "thousand": "thousands",
    "thousands": "thousands",
    "000": "thousands",
    "rs 000": "thousands",
    "pkr 000": "thousands",
    "million": "millions",
    "millions": "millions",
}


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def parse_number(s: str) -> float | None:
    if not s or s.strip() in ('', '-', '—', 'N/A', 'n/a'):
        return None
    s = s.strip().replace('**', '').replace(',', '').replace(' ', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def normalize_unit_type(unit_type: str | None) -> tuple[str, str | None]:
    raw = (unit_type or "thousands").lower().strip()
    if raw in VALID_UNITS:
        return ("rupees" if raw == "rupee" else raw, None)

    cleaned = re.sub(r"\([^)]*\)", " ", raw)
    parts = [
        part.strip(" .:-_")
        for part in re.split(r"\s*(?:\||/|,|;|\band\b|\bexcept\b)\s*", cleaned)
        if part.strip(" .:-_")
    ]
    mapped = [UNIT_ALIASES[part] for part in parts if part in UNIT_ALIASES]
    if not mapped:
        for key, value in UNIT_ALIASES.items():
            if re.search(rf"\b{re.escape(key)}\b", cleaned):
                mapped.append(value)

    # Prefer explicit scale words over currency tokens. "millions | rupees"
    # means values are reported in millions of rupees, not full rupees.
    for candidate in ("millions", "thousands", "full_rupees", "rupees"):
        if candidate in mapped:
            canonical = candidate
            break
    else:
        return raw, "invalid_unit_type"

    distinct_scales = {item for item in mapped if item in {"millions", "thousands", "full_rupees"}}
    if len(distinct_scales) > 1:
        if "thousands" in distinct_scales and "millions" in distinct_scales:
            return "thousands", "ambiguous_unit_type"
        return canonical, "ambiguous_unit_type"
    return canonical, None


def unit_issue(unit_type: str) -> str | None:
    unit, issue = normalize_unit_type(unit_type)
    if unit in VALID_UNITS:
        return issue
    if "rupee" in unit and "thousand" in unit:
        return "ambiguous_unit_type"
    return "invalid_unit_type"


def unit_message(unit_type: str) -> str:
    return (
        f"Extraction emitted UNIT_TYPE={unit_type!r}. This is not a canonical unit label; "
        "normalize/flatten can rescale if rupees is present, but the source should be re-extracted "
        "or reviewed before publishing."
    )


def parse_period_column(col: str) -> dict | None:
    """Parse "12M Dec 2023" style period column header."""
    col = col.strip()
    m = re.match(r'(\d+)M\s+(\w+)\s+(\d{4})', col)
    if not m:
        return None
    dur_n = int(m.group(1))
    month_str = m.group(2).lower()
    year = int(m.group(3))
    month = MONTH_MAP.get(month_str)
    if not month:
        return None
    day = MONTH_DAYS[month]
    if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        day = 29
    return {
        'duration': f"{dur_n}M",
        'duration_months': dur_n,
        'month': m.group(2),
        'year': year,
        'period_end': f"{year}-{month:02d}-{day:02d}",
        'original': col,
    }


def parse_date(date_str: str) -> str | None:
    """Parse "30 Jun 2024" style date column header to ISO YYYY-MM-DD."""
    date_str = date_str.strip().replace('**', '')
    patterns = [
        (r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})', '%d %b %Y'),
        (r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', '%d %B %Y'),
        (r'(\d{1,2})-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{2,4})', '%d-%b-%y'),
    ]
    for pattern, fmt in patterns:
        match = re.search(pattern, date_str, re.IGNORECASE)
        if match:
            try:
                date_part = f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if '%y' in fmt else f"{match.group(1)} {match.group(2)} {match.group(3)}"
                dt = datetime.strptime(date_part, fmt)
                if dt.year < 100:
                    dt = dt.replace(year=dt.year + 2000)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue
    return None


def parse_filename(filename: str) -> dict | None:
    """Parse "TICKER_annual|quarterly_DATE_consolidation.md" filenames."""
    name = filename.replace('.md', '')
    parts = name.split('_')
    if len(parts) < 4:
        return None
    ticker = parts[0]
    period_type = parts[1]
    if period_type == 'annual':
        year, consolidation = parts[2], parts[3]
        filing_date = f"{year}-12-31"
    else:
        filing_date, consolidation = parts[2], parts[3]
    return {'ticker': ticker, 'period_type': period_type, 'filing_date': filing_date,
            'consolidation': consolidation, 'filename': filename}


def is_current_period(candidate: dict) -> bool:
    try:
        period_end = datetime.strptime(candidate['period_end'], '%Y-%m-%d')
        filing_date = datetime.strptime(candidate['filing_date'], '%Y-%m-%d')
        months_diff = (filing_date.year - period_end.year) * 12 + (filing_date.month - period_end.month)
        return months_diff <= 13
    except Exception:
        return True


def load_qc_results(qc_file: Path) -> dict:
    if not qc_file.exists():
        print(f"Warning: QC results not found at {qc_file}")
        return {}
    data = json.loads(qc_file.read_text())
    qc_status = {}
    result_rows = data.get('results', data.get('files', []))
    if isinstance(result_rows, int):
        result_rows = []
    for result in result_rows:
        fname = result.get('file') or result.get('source_file')
        if not fname:
            continue
        status = result.get('status', 'unknown')
        status = result.get('qc_status', status)
        qc_status[fname] = 'pass' if status == 'pass' else ('no_formulas' if result.get('formulas', 0) == 0 else 'fail')
    return qc_status


def get_source_pages(ticker: str, period_type: str, filing_date: str, consolidation: str, stmt_key: str) -> dict:
    if period_type == 'annual':
        year = filing_date[:4]
        filing_period = f"annual_{year}"
        folder = f"{ticker}/{year}/{ticker}_Annual_{year}"
    else:
        filing_period = f"quarterly_{filing_date}"
        year = filing_date[:4]
        folder = f"{ticker}/{year}/{ticker}_Quarterly_{filing_date}"
    pages = []
    if ticker in STATEMENT_PAGES:
        td = STATEMENT_PAGES[ticker]
        if filing_period in td and consolidation in td[filing_period]:
            pages = td[filing_period][consolidation].get(stmt_key, [])
    return {'source_pages': pages, 'source_url': f"{PDF_BASE_URL}/{folder}"}


# ---------------------------------------------------------------------------
# PL / CF — period-column format
# ---------------------------------------------------------------------------

def parse_markdown_table(filepath: Path) -> dict | None:
    """Parse Ref-column markdown table into periods + rows. Used by both PL and CF."""
    content = filepath.read_text()
    lines = content.split('\n')
    result = {'unit_type': 'thousands', 'periods': [], 'rows': []}
    for line in lines:
        if line.startswith('UNIT_TYPE:'):
            result['unit_type'] = line.split(':', 1)[1].strip()
            break
    in_table = False
    for line in lines:
        line = line.strip()
        if not line.startswith('|'):
            continue
        if '---' in line:
            in_table = True
            continue
        parts = [p.strip() for p in line.split('|')]
        if parts and parts[0] == '':
            parts = parts[1:]
        if parts and parts[-1] == '':
            parts = parts[:-1]
        if not in_table:
            if len(parts) >= 4:
                for i in range(3, len(parts)):
                    pi = parse_period_column(parts[i])
                    if pi:
                        result['periods'].append(pi)
            continue
        if len(parts) < 4:
            continue
        source_item = parts[0].replace('**', '')
        canonical = parts[1].replace('**', '')
        ref_raw = parts[2].replace('**', '')
        if 'Source Item' in source_item or 'Canonical' in canonical or not canonical.strip():
            continue
        ref_name, formula = ref_raw, None
        if '=' in ref_raw:
            ref_name, formula = ref_raw.split('=', 1)
            ref_name, formula = ref_name.strip(), formula.strip()
        values = {}
        for i, pi in enumerate(result['periods']):
            col_idx = 3 + i
            if col_idx < len(parts):
                val = parse_number(parts[col_idx])
                if val is not None:
                    values[(pi['period_end'], pi['duration'])] = val
        if values:
            result['rows'].append({'canonical': canonical, 'source_item': source_item,
                                   'ref': ref_name, 'formula': formula,
                                   'is_calculated': formula is not None, 'values': values})
    return result


def select_best_source_flow(candidates: list[dict], qc_status: dict, priority_field: str | None = None) -> dict:
    """Pick best source for a (period_end, duration, consolidation) slot.
    priority_field: if set, candidates that have this field get +100 score (used by PL for 'taxation')."""
    if len(candidates) == 1:
        best = candidates[0]
        best['source_type'] = 'primary' if is_current_period(best) else 'prior_year'
        return best

    def passes_qc(c: dict) -> bool:
        return qc_status.get(c.get('source_file', ''), 'unknown') in ('pass', 'no_formulas')

    def score(c: dict) -> int:
        s = 0
        if priority_field and c.get('values', {}).get(priority_field) is not None:
            s += 100
        if passes_qc(c):
            s += 50
        return s

    current = [c for c in candidates if is_current_period(c)]
    prior = [c for c in candidates if not is_current_period(c)]
    current_sorted = sorted(current, key=lambda c: (score(c), c.get('filing_date', '')), reverse=True)
    prior_sorted = sorted(prior, key=lambda c: (score(c), c.get('filing_date', '')), reverse=True)

    for pool, src_type in [
        (iter(c for c in current_sorted if passes_qc(c)), 'primary'),
        (iter(c for c in prior_sorted if passes_qc(c)), 'prior_year_fallback'),
        (iter(current_sorted), 'primary'),
        (iter(prior_sorted), 'prior_year_fallback'),
    ]:
        try:
            best = next(pool)
            best['source_type'] = src_type if isinstance(src_type, str) else ('primary' if is_current_period(best) else 'prior_year_fallback')
            return best
        except StopIteration:
            continue
    candidates[0]['source_type'] = 'primary'
    return candidates[0]


def process_ticker_flow(ticker: str, files: list[Path], qc_status: dict, stmt_key: str,
                        overwrite_fields: set, priority_field: str | None, verbose: bool) -> dict:
    result = {'ticker': ticker, 'generated_at': datetime.now().isoformat(), 'periods': []}
    period_candidates: dict = defaultdict(list)

    for filepath in sorted(files):
        file_info = parse_filename(filepath.name)
        if not file_info:
            continue
        parsed = parse_markdown_table(filepath)
        if not parsed or not parsed['rows']:
            continue
        consolidation = file_info['consolidation']
        filing_date = file_info['filing_date']
        qc_result = qc_status.get(filepath.name, 'unknown')
        source_info = get_source_pages(ticker, file_info['period_type'], filing_date, consolidation, stmt_key)

        for pi in parsed['periods']:
            period_end, duration = pi['period_end'], pi['duration']
            period_key = (period_end, duration)
            values: dict = {}
            source_items: dict = {}
            refs: dict = {}
            is_subtotal_flag: dict = {}

            for row in parsed['rows']:
                if period_key not in row['values']:
                    continue
                canonical = row['canonical']
                raw_value = row['values'][period_key]
                is_authoritative = row['is_calculated'] or canonical in overwrite_fields

                if canonical in values:
                    if is_authoritative:
                        values[canonical] = raw_value
                        source_items[canonical] = row['source_item']
                        is_subtotal_flag[canonical] = True
                        refs[canonical] = f"{row['ref']}={row['formula']}" if row['formula'] else row['ref']
                    elif not is_subtotal_flag.get(canonical):
                        existing, new_val = values[canonical] or 0, raw_value or 0
                        if existing != new_val:
                            values[canonical] = existing + new_val
                            source_items[canonical] += f"; {row['source_item']}"
                            if row['ref']:
                                refs[canonical] = refs.get(canonical, '') + '+' + row['ref']
                else:
                    values[canonical] = raw_value
                    source_items[canonical] = row['source_item']
                    is_subtotal_flag[canonical] = is_authoritative
                    refs[canonical] = f"{row['ref']}={row['formula']}" if row['formula'] else row.get('ref', '')

            if values:
                dedup_key = (consolidation, period_end, duration)
                canonical_unit, _unit_issue = normalize_unit_type(parsed['unit_type'])
                period_candidates[dedup_key].append({
                    'period_end': period_end, 'duration': duration,
                    'duration_months': pi['duration_months'], 'year': pi['year'],
                    'consolidation': consolidation, 'period_type': file_info['period_type'],
                    'filing_date': filing_date, 'source_file': filepath.name,
                    'source_qc_status': qc_result,
                    'source_pages': source_info['source_pages'], 'source_url': source_info['source_url'],
                    'unit_type': canonical_unit, 'raw_unit_type': parsed['unit_type'], 'values': values,
                    'source_items': source_items, 'refs': refs,
                })

    for dedup_key in sorted(period_candidates.keys()):
        candidates = period_candidates[dedup_key]
        best = select_best_source_flow(candidates, qc_status, priority_field)
        result['periods'].append(best)
        if verbose:
            cons, pe, dur = dedup_key
            n = len(candidates)
            print(f"  {cons} {pe} {dur}: {len(best['values'])} values [{best['source_qc_status']}]"
                  + (f" (from {n} candidates)" if n > 1 else ""))

    result['periods'].sort(key=lambda p: (p['consolidation'], p['period_end'], p['duration_months']))
    return result


def run_pl(ticker_filter: str | None, verbose: bool) -> None:
    input_dir = PROJECT_ROOT / "data" / "extracted_pl"
    output_dir = PROJECT_ROOT / "data" / "json_pl"
    qc_file = PROJECT_ROOT / "artifacts" / "09_qc" / "pl_results.json"
    if not input_dir.exists():
        print(f"ERROR: {input_dir} not found"); return
    output_dir.mkdir(parents=True, exist_ok=True)
    qc_status = load_qc_results(qc_file)
    files = sorted(input_dir.glob("*.md"))
    files_by_ticker: dict = defaultdict(list)
    for f in files:
        fi = parse_filename(f.name)
        if fi:
            files_by_ticker[fi['ticker']].append(f)
    if ticker_filter:
        files_by_ticker = {k: v for k, v in files_by_ticker.items() if k == ticker_filter}
    print(f"[pl] {len(files_by_ticker)} tickers")
    for ticker in sorted(files_by_ticker):
        if verbose:
            print(f"\n{ticker}:")
        result = process_ticker_flow(ticker, files_by_ticker[ticker], qc_status,
                                     stmt_key='PL', overwrite_fields=PL_OVERWRITE_FIELDS,
                                     priority_field='taxation', verbose=verbose)
        (output_dir / f"{ticker}.json").write_text(json.dumps(result, indent=2))
        TYPE_COUNTS["pl"]["tickers"] += 1
        TYPE_COUNTS["pl"]["periods"] += len(result["periods"])
        for p in result["periods"]:
            row = {"ticker": ticker, "statement_type": "profit_loss",
                   "period_end": p["period_end"], "period_duration": p["duration"],
                   "section": p["consolidation"], "source_file": p["source_file"],
                   "source_pages": p.get("source_pages", []), "source_qc_status": p.get("source_qc_status"),
                   "source_type": p.get("source_type"), "values": len(p.get("values", {})),
                   "origin_stage": "09_qc"}
            NORMALIZE_LINEAGE.append(row)
            unit_rule = unit_issue(p.get("unit_type", "thousands"))
            if unit_rule:
                NORMALIZE_ISSUES.append({**row, "severity": "warning" if unit_rule == "ambiguous_unit_type" else "blocker",
                                         "rule": unit_rule,
                                         "unit_type": p.get("unit_type"),
                                         "message": unit_message(p.get("unit_type", "")),
                                         "origin_stage": "08_extract",
                                         "recommended_command": f"python3 pipeline/08_extract.py --type pl --ticker {ticker} --source pdf --reset && python3 pipeline/09_qc.py --type pl --ticker {ticker}"})
            if p.get("source_qc_status") == "fail":
                NORMALIZE_ISSUES.append({**row, "severity": "blocker",
                                         "rule": "selected_failed_qc_source",
                                         "message": "Normalize selected an extraction source that failed Step 09 QC.",
                                         "recommended_command": f"python3 pipeline/08_extract.py --type pl --ticker {ticker} --source pdf --repair-failures --reset && python3 pipeline/09_qc.py --type pl --ticker {ticker}"})
        if not verbose:
            print(f"  {ticker}: {len(result['periods'])} periods")


def run_cf(ticker_filter: str | None, verbose: bool) -> None:
    input_dir = PROJECT_ROOT / "data" / "extracted_cf"
    output_dir = PROJECT_ROOT / "data" / "json_cf"
    qc_file = PROJECT_ROOT / "artifacts" / "09_qc" / "cf_results.json"
    if not input_dir.exists():
        print(f"ERROR: {input_dir} not found"); return
    output_dir.mkdir(parents=True, exist_ok=True)
    qc_status = load_qc_results(qc_file)
    files = sorted(input_dir.glob("*.md"))
    files_by_ticker: dict = defaultdict(list)
    for f in files:
        fi = parse_filename(f.name)
        if fi:
            files_by_ticker[fi['ticker']].append(f)
    if ticker_filter:
        files_by_ticker = {k: v for k, v in files_by_ticker.items() if k == ticker_filter}
    print(f"[cf] {len(files_by_ticker)} tickers")
    for ticker in sorted(files_by_ticker):
        if verbose:
            print(f"\n{ticker}:")
        result = process_ticker_flow(ticker, files_by_ticker[ticker], qc_status,
                                     stmt_key='CF', overwrite_fields=set(),
                                     priority_field=None, verbose=verbose)
        (output_dir / f"{ticker}.json").write_text(json.dumps(result, indent=2))
        TYPE_COUNTS["cf"]["tickers"] += 1
        TYPE_COUNTS["cf"]["periods"] += len(result["periods"])
        for p in result["periods"]:
            row = {"ticker": ticker, "statement_type": "cash_flow",
                   "period_end": p["period_end"], "period_duration": p["duration"],
                   "section": p["consolidation"], "source_file": p["source_file"],
                   "source_pages": p.get("source_pages", []), "source_qc_status": p.get("source_qc_status"),
                   "source_type": p.get("source_type"), "values": len(p.get("values", {})),
                   "origin_stage": "09_qc"}
            NORMALIZE_LINEAGE.append(row)
            unit_rule = unit_issue(p.get("unit_type", "thousands"))
            if unit_rule:
                NORMALIZE_ISSUES.append({**row, "severity": "warning" if unit_rule == "ambiguous_unit_type" else "blocker",
                                         "rule": unit_rule,
                                         "unit_type": p.get("unit_type"),
                                         "message": unit_message(p.get("unit_type", "")),
                                         "origin_stage": "08_extract",
                                         "recommended_command": f"python3 pipeline/08_extract.py --type cf --ticker {ticker} --source pdf --reset && python3 pipeline/09_qc.py --type cf --ticker {ticker}"})
            if p.get("source_qc_status") == "fail":
                NORMALIZE_ISSUES.append({**row, "severity": "blocker",
                                         "rule": "selected_failed_qc_source",
                                         "message": "Normalize selected an extraction source that failed Step 09 QC.",
                                         "recommended_command": f"python3 pipeline/08_extract.py --type cf --ticker {ticker} --source pdf --repair-failures --reset && python3 pipeline/09_qc.py --type cf --ticker {ticker}"})
        if not verbose:
            print(f"  {ticker}: {len(result['periods'])} periods")


# ---------------------------------------------------------------------------
# BS — date-column format (genuinely different table structure)
# ---------------------------------------------------------------------------

BS_FIELD_ALIASES = [
    ('subtotal_equity', 'total_equity'),
    ('total_liabilities_and_equity', 'total_equity_and_liabilities'),
    ('cash_and_bank_balances', 'cash_and_equivalents'),
    ('cash_and_balances_with_treasury_banks', 'cash_and_equivalents'),
    ('cash', 'cash_and_equivalents'),
    ('balances_with_other_banks', 'bank_balances'),
]


def load_bs_exclusions() -> set:
    excl_file = PROJECT_ROOT / "artifacts" / "allowlists" / "bs_exclusions.json"
    if not excl_file.exists():
        return set()
    data = json.loads(excl_file.read_text())
    return {item["file"] for item in data.get("exclude", [])}


def passes_qc_bs(filename: str, qc_status: dict) -> bool:
    return qc_status.get(filename, 'unknown') in ('pass', 'no_formulas')


def parse_bs_file(filepath: Path) -> dict | None:
    content = filepath.read_text(encoding='utf-8')
    lines = content.split('\n')
    fname = filepath.stem
    parts = fname.rsplit('_', 1)
    if len(parts) != 2:
        return None
    section = parts[1]
    ticker_period = parts[0]
    result = {"ticker": None, "filing_period": None, "filing_type": None,
              "section": section, "unit_type": "rupees", "periods": {}}
    for sep in ['_annual_', '_quarterly_']:
        if sep in ticker_period:
            idx = ticker_period.index(sep)
            result["ticker"] = ticker_period[:idx]
            result["filing_period"] = ticker_period[idx + 1:]
            result["filing_type"] = "annual" if "_annual_" in sep else "quarterly"
            break
    if not result["ticker"]:
        return None

    # Derive a ceiling date from the filing period so we can reject future comparative columns.
    # A BS column dated after the filing date is physically impossible (can't be audited yet).
    filing_period = result["filing_period"]  # e.g. "quarterly_2026-03-31" or "annual_2025"
    filing_date_str = re.search(r'(\d{4}-\d{2}-\d{2})', filing_period)
    filing_ceiling: str | None = filing_date_str.group(1) if filing_date_str else None

    date_columns: list[str] = []
    for line in lines:
        line = line.strip()
        if line.startswith('UNIT_TYPE:'):
            result["unit_type"] = line.split(':')[1].strip().lower()
            continue
        if not line.startswith('|'):
            continue
        cols = [c.strip() for c in line.split('|')]
        if len(cols) < 5:
            continue
        if 'Source Item' in line or 'Canonical' in line:
            for col in cols[4:]:
                d = parse_date(col)
                if d:
                    if filing_ceiling and d > filing_ceiling:
                        continue  # future date — PDF typo (e.g. 2026 instead of 2025)
                    date_columns.append(d)
                    result["periods"][d] = {"values": {}, "source_items": {}, "refs": {}}
            continue
        if line.startswith('|:') or line.startswith('|-'):
            continue
        if len(cols) >= 5 and date_columns:
            source_item = cols[1].replace('**', '').strip()
            canonical = cols[2].replace('**', '').replace('[', '').replace(']', '').strip().lower()
            ref = cols[3].strip() if len(cols) > 3 else ""
            if not canonical or canonical in ('canonical', 'ref'):
                continue
            is_subtotal = '=' in ref or canonical.startswith('total_')
            for i, date in enumerate(date_columns):
                if i + 4 < len(cols):
                    value = parse_number(cols[i + 4])
                    if value is not None:
                        pd_ = result["periods"][date]
                        if canonical in pd_["values"]:
                            existing_sub = pd_.get("_is_subtotal", {}).get(canonical, False)
                            if is_subtotal:
                                pd_["values"][canonical] = value
                                pd_["source_items"][canonical] = source_item
                                pd_.setdefault("_is_subtotal", {})[canonical] = True
                                if ref:
                                    pd_["refs"][canonical] = ref
                            elif not existing_sub:
                                ex, nv = pd_["values"][canonical] or 0, value or 0
                                if ex != nv:
                                    pd_["values"][canonical] = ex + nv
                                    pd_["source_items"][canonical] += f"; {source_item}"
                        else:
                            pd_["values"][canonical] = value
                            pd_["source_items"][canonical] = source_item
                            pd_.setdefault("_is_subtotal", {})[canonical] = is_subtotal
                            if ref:
                                pd_["refs"][canonical] = ref
    return result if result["ticker"] and result["periods"] else None


def get_source_info_bs(ticker: str, filing_period: str, section: str) -> dict:
    if filing_period.startswith('annual_'):
        year = filing_period.replace('annual_', '')
        folder = f"{ticker}/{year}/{ticker}_Annual_{year}"
    else:
        date_part = filing_period.replace('quarterly_', '')
        year = date_part[:4]
        folder = f"{ticker}/{year}/{ticker}_Quarterly_{date_part}"
    pages = []
    if ticker in STATEMENT_PAGES:
        td = STATEMENT_PAGES[ticker]
        if filing_period in td and section in td[filing_period]:
            pages = td[filing_period][section].get('BS', [])
    return {'source_pages': pages, 'source_url': f"{PDF_BASE_URL}/{folder}"}


def get_primary_date_bs(filing_period: str) -> str | None:
    if filing_period.startswith('annual_'):
        return f"{filing_period.replace('annual_', '')}-06-30"
    elif filing_period.startswith('quarterly_'):
        return filing_period.replace('quarterly_', '')
    return None


def run_bs(ticker_filter: str | None, verbose: bool) -> None:
    input_dir = PROJECT_ROOT / "data" / "extracted_bs"
    output_dir = PROJECT_ROOT / "data" / "json_bs"
    qc_file = PROJECT_ROOT / "artifacts" / "09_qc" / "bs_results.json"
    if not input_dir.exists():
        print(f"ERROR: {input_dir} not found"); return
    output_dir.mkdir(parents=True, exist_ok=True)
    exclusions = load_bs_exclusions()
    qc_status = load_qc_results(qc_file)
    files = [f for f in sorted(input_dir.glob("*.md"))
             if f.name not in exclusions and (not ticker_filter or f.name.startswith(f"{ticker_filter}_"))]
    print(f"[bs] {len(files)} files")

    ticker_candidates: dict = defaultdict(lambda: defaultdict(list))
    stats = {"processed": 0, "skipped": 0, "periods_added": 0, "periods_dedupe": 0, "periods_fallback": 0,
             "periods_pass": 0, "periods_fail": 0, "periods_unknown": 0}

    for filepath in files:
        parsed = parse_bs_file(filepath)
        if not parsed:
            stats["skipped"] += 1
            continue
        ticker = parsed["ticker"]
        section = parsed["section"]
        unit_type, _unit_issue = normalize_unit_type(parsed["unit_type"])
        filing_period = parsed["filing_period"]
        filing_type = parsed["filing_type"]
        primary_date = get_primary_date_bs(filing_period)
        source_info = get_source_info_bs(ticker, filing_period, section)
        stats["processed"] += 1

        for date, period_data in parsed["periods"].items():
            raw_values = dict(period_data["values"])
            source_items = dict(period_data["source_items"])
            refs = dict(period_data.get("refs", {}))
            for alias, canonical in BS_FIELD_ALIASES:
                if alias in raw_values and canonical not in raw_values:
                    raw_values[canonical] = raw_values.pop(alias)
                    if alias in source_items:
                        source_items[canonical] = source_items.pop(alias)
                    if alias in refs:
                        refs[canonical] = refs.pop(alias)
            # Derive missing totals
            if raw_values.get('total_liabilities') is None:
                tcl = raw_values.get('total_current_liabilities')
                tncl = raw_values.get('total_non_current_liabilities')
                if tcl is not None and tncl is not None:
                    raw_values['total_liabilities'] = tcl + tncl
                    source_items['total_liabilities'] = '[Derived: TCL + TNCL]'
            if raw_values.get('total_assets') is None:
                nca = raw_values.get('total_non_current_assets') or raw_values.get('total_non_current_assets_sub')
                ca = raw_values.get('total_current_assets')
                if nca is not None and ca is not None:
                    raw_values['total_assets'] = nca + ca
                    source_items['total_assets'] = '[Derived: NCA + CA]'
            te = raw_values.get('total_equity')
            tl = raw_values.get('total_liabilities')
            ta = raw_values.get('total_assets')
            if te is not None and tl is not None:
                expected_tel = te + tl
                current_tel = raw_values.get('total_equity_and_liabilities')
                if current_tel is None:
                    raw_values['total_equity_and_liabilities'] = expected_tel
                    source_items['total_equity_and_liabilities'] = '[Derived: TE + TL]'
                elif ta is not None and expected_tel > 0:
                    if abs(current_tel - expected_tel) / expected_tel > 0.05 and ta > 0 and abs(expected_tel - ta) / ta < 0.05:
                        raw_values['total_equity_and_liabilities'] = expected_tel
                        source_items['total_equity_and_liabilities'] = f'[Corrected: TE + TL, was {current_tel}]'
            if raw_values.get('total_assets') is None and raw_values.get('total_equity_and_liabilities') is not None:
                raw_values['total_assets'] = raw_values['total_equity_and_liabilities']
                source_items['total_assets'] = '[Derived: TA = TEL]'

            key = (date, section)
            fname = filepath.name
            ticker_candidates[ticker][key].append({
                "period_end": date, "consolidation": section, "filing_type": filing_type,
                "filing_period": filing_period, "unit_type": unit_type, "raw_unit_type": parsed["unit_type"], "source_file": fname,
                "source_pages": source_info['source_pages'], "source_url": source_info['source_url'],
                "is_primary": (date == primary_date),
                "passes_qc": passes_qc_bs(fname, qc_status),
                "source_qc_status": qc_status.get(fname, 'unknown'),
                "values": raw_values, "source_items": source_items, "refs": refs,
            })

    ticker_periods: dict = {}
    for ticker in sorted(ticker_candidates.keys()):
        ticker_periods[ticker] = {}
        for key, candidates in ticker_candidates[ticker].items():
            if len(candidates) == 1:
                best = candidates[0]
                best['source_type'] = 'primary' if best['is_primary'] else 'prior_year'
            else:
                primary_c = [c for c in candidates if c['is_primary']]
                prior_c = [c for c in candidates if not c['is_primary']]
                primary_pass = [c for c in primary_c if c['passes_qc']]
                prior_pass = [c for c in prior_c if c['passes_qc']]
                if primary_pass:
                    best = primary_pass[0]; best['source_type'] = 'primary'
                    stats["periods_dedupe"] += len(candidates) - 1
                elif prior_pass:
                    best = prior_pass[0]; best['source_type'] = 'prior_year_fallback'
                    stats["periods_dedupe"] += len(candidates) - 1; stats["periods_fallback"] += 1
                    if verbose:
                        print(f"  FALLBACK: {ticker}/{key[1]}/{key[0]} using prior-year {best['source_file']}")
                elif primary_c:
                    best = primary_c[0]; best['source_type'] = 'primary'
                    stats["periods_dedupe"] += len(candidates) - 1
                else:
                    best = (prior_c or candidates)[0]; best['source_type'] = 'prior_year'
                    stats["periods_dedupe"] += len(candidates) - 1
            ticker_periods[ticker][key] = best
            stats["periods_added"] += 1
            qc_stat = best.get('source_qc_status', 'unknown')
            if qc_stat in ('pass', 'no_formulas'):
                stats["periods_pass"] += 1
            elif qc_stat == 'fail':
                stats["periods_fail"] += 1
            else:
                stats["periods_unknown"] += 1

    for ticker in sorted(ticker_periods.keys()):
        periods = list(ticker_periods[ticker].values())
        periods.sort(key=lambda p: (p["period_end"], p["consolidation"]))
        for p in periods:
            p.pop("is_primary", None); p.pop("passes_qc", None)
        (output_dir / f"{ticker}.json").write_text(json.dumps({"ticker": ticker, "periods": periods}, indent=2))
        TYPE_COUNTS["bs"]["tickers"] += 1
        TYPE_COUNTS["bs"]["periods"] += len(periods)
        for p in periods:
            row = {"ticker": ticker, "statement_type": "balance_sheet",
                   "period_end": p["period_end"], "period_duration": "PIT",
                   "section": p["consolidation"], "source_file": p["source_file"],
                   "source_pages": p.get("source_pages", []), "source_qc_status": p.get("source_qc_status"),
                   "source_type": p.get("source_type"), "values": len(p.get("values", {})),
                   "origin_stage": "09_qc"}
            NORMALIZE_LINEAGE.append(row)
            unit_rule = unit_issue(p.get("unit_type", "thousands"))
            if unit_rule:
                NORMALIZE_ISSUES.append({**row, "severity": "warning" if unit_rule == "ambiguous_unit_type" else "blocker",
                                         "rule": unit_rule,
                                         "unit_type": p.get("unit_type"),
                                         "message": unit_message(p.get("unit_type", "")),
                                         "origin_stage": "08_extract",
                                         "recommended_command": f"python3 pipeline/08_extract.py --type bs --ticker {ticker} --source pdf --reset && python3 pipeline/09_qc.py --type bs --ticker {ticker}"})
            if p.get("source_qc_status") == "fail":
                NORMALIZE_ISSUES.append({**row, "severity": "blocker",
                                         "rule": "selected_failed_qc_source",
                                         "message": "Normalize selected an extraction source that failed Step 09 QC.",
                                         "recommended_command": f"python3 pipeline/08_extract.py --type bs --ticker {ticker} --source pdf --repair-failures --reset && python3 pipeline/09_qc.py --type bs --ticker {ticker}"})
        print(f"  {ticker}: {len(periods)} periods")

    print(f"\n[bs] processed={stats['processed']} periods={stats['periods_added']} "
          f"qc_pass={stats['periods_pass']} qc_fail={stats['periods_fail']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Step 10 — normalize extracted md -> per-ticker json")
    ap.add_argument("--type", choices=["pl", "bs", "cf"], help="statement type (default: all)")
    ap.add_argument("--ticker")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    for t in ([a.type] if a.type else ["pl", "bs", "cf"]):
        print(f"\n{'=' * 60}\n[{t.upper()}] normalize\n{'=' * 60}")
        {"pl": run_pl, "bs": run_bs, "cf": run_cf}[t](a.ticker, a.verbose)
    write_stage_lineage("10_normalize", NORMALIZE_LINEAGE)
    write_stage_issues("10_normalize", NORMALIZE_ISSUES)
    report = {"type": a.type or "all", "ticker_filter": a.ticker,
              "counts": {k: dict(v) for k, v in TYPE_COUNTS.items()},
              "lineage_rows": len(NORMALIZE_LINEAGE), "issues": len(NORMALIZE_ISSUES)}
    write_report("10_normalize", report)
    unit_issues = sum(1 for issue in NORMALIZE_ISSUES if issue.get("rule") in {"ambiguous_unit_type", "invalid_unit_type"})
    failed_qc = sum(1 for issue in NORMALIZE_ISSUES if issue.get("rule") == "selected_failed_qc_source")
    emit_decision("10_normalize", f"Normalized {sum(v['periods'] for v in TYPE_COUNTS.values()):,} periods; "
                  f"{failed_qc:,} selected sources failed upstream QC; {unit_issues:,} unit labels need review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
