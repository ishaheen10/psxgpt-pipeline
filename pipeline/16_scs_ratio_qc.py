#!/usr/bin/env python3
"""Step 16 SCS ratio QC — compare direct valuation ratios to SCSTrade.

This is an external-reference check only. PSX/D1 remains authoritative for
market close and market cap; SCS is used to spot suspicious P/E and dividend
yield differences against our own financial-statement calculations.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import ARTIFACTS, PROJECT_ROOT  # noqa: E402
from shared.report import emit_decision, write_report  # noqa: E402

ARTIFACTS_DIR = ARTIFACTS / "16_scs_ratio_qc"
QUALITY_FLAGS_PATH = ARTIFACTS_DIR / "data_quality_flags.jsonl"
D1_DB = "psx"
SCS_ENDPOINT = "https://www.scstrade.com/MarketStatistics/MS_MarketValuations.aspx/resdata"
SCS_PAGES = {
    "all_market": {
        "id": "-1",
        "name": "Market Valuations",
        "url": "https://www.scstrade.com/MarketStatistics/MS_MarketValuationsMain.aspx",
    },
    "dividend_yield": {
        "id": "-14",
        "name": "Top 40 Highest Dividend Yield Stocks",
        "url": "https://www.scstrade.com/MarketStatistics/MS_MarketValuations.aspx?sectorid=-14&name=Top%2040%20Highest%20Dividend%20Yield%20Stocks",
    },
    "pe_ratio": {
        "id": "-20",
        "name": "Top 40 Lowest P/E Stocks",
        "url": "https://www.scstrade.com/MarketStatistics/MS_MarketValuations.aspx?sectorid=-20&name=Top%2040%20Lowest%20P%2FE%20Stocks",
    },
}
SCS_SECTOR_IDS = {
    "sector_34": "34",
    "sector_35": "35",
    "sector_36": "36",
    "sector_37": "37",
    "sector_38": "38",
    "sector_39": "39",
    "sector_40": "40",
    "sector_41": "41",
    "sector_42": "42",
    "sector_43": "43",
    "sector_45": "45",
    "sector_46": "46",
    "sector_47": "47",
    "sector_49": "49",
    "sector_50": "50",
    "sector_51": "51",
    "sector_52": "52",
    "sector_53": "53",
    "sector_54": "54",
    "sector_55": "55",
    "sector_56": "56",
    "sector_57": "57",
    "sector_58": "58",
    "sector_59": "59",
    "sector_60": "60",
    "sector_61": "61",
    "sector_62": "62",
    "sector_63": "63",
    "sector_64": "64",
    "sector_65": "65",
    "sector_66": "66",
    "sector_67": "67",
    "sector_68": "68",
    "sector_69": "69",
}
for source, sector_id in SCS_SECTOR_IDS.items():
    SCS_PAGES[source] = {
        "id": sector_id,
        "name": f"SCS sector {sector_id}",
        "url": f"https://www.scstrade.com/MarketStatistics/MS_MarketValuations.aspx?sectorid={sector_id}",
    }

UNIT_SCALE = {
    "ones": 1.0,
    "actual": 1.0,
    "rupees": 1.0,
    "thousands": 1_000.0,
    "million": 1_000_000.0,
    "millions": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "billions": 1_000_000_000.0,
}


@dataclass
class ScsReference:
    ticker: str
    name: str | None
    source: str
    scs_price: float | None
    scs_pe: float | None
    scs_dividend_yield_pct: float | None
    scs_eps_annual: float | None
    scs_eps_latest: str | None


@dataclass
class DirectInputs:
    ticker: str
    market_date: str | None = None
    closing_price: float | None = None
    market_cap: float | None = None
    shares: float | None = None
    market_dividend_yield_pct: float | None = None
    net_profit_period_end: str | None = None
    net_profit_duration: str | None = None
    net_profit_section: str | None = None
    net_profit_unit_type: str | None = None
    net_profit_value: float | None = None
    net_profit_amount: float | None = None
    net_profit_scale_label: str | None = None
    net_profit_candidates: list[dict[str, Any]] | None = None
    default_net_profit_period_end: str | None = None
    default_net_profit_duration: str | None = None
    default_net_profit_section: str | None = None
    default_net_profit_unit_type: str | None = None
    default_net_profit_value: float | None = None
    default_net_profit_amount: float | None = None
    default_net_profit_pe: float | None = None
    net_profit_selection_reason: str | None = None
    dividends_paid_period_end: str | None = None
    dividends_paid_duration: str | None = None
    dividends_paid_section: str | None = None
    dividends_paid_unit_type: str | None = None
    dividends_paid_value: float | None = None
    dividends_paid_amount: float | None = None
    dividends_paid_per_share: float | None = None
    dividend_yield_candidates: list[dict[str, Any]] | None = None


@dataclass
class Comparison:
    ticker: str
    name: str | None
    sources: list[str]
    status: str
    notes: list[str]
    scs_price: float | None
    d1_closing_price: float | None
    close_diff_pct: float | None
    scs_pe: float | None
    direct_pe: float | None
    pe_abs_diff: float | None
    pe_diff_pct: float | None
    scs_dividend_yield_pct: float | None
    direct_dividend_yield_pct: float | None
    direct_dividend_yield_basis: str | None
    scs_implied_dividend_per_share: float | None
    dividends_paid_yield_pct: float | None
    dividend_yield_abs_diff_pp: float | None
    dividend_yield_diff_pct: float | None
    inputs: DirectInputs


def stable_flag_id(*parts: Any) -> str:
    body = "|".join(str(part or "") for part in parts)
    return "scs_ratio_" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def display_value(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value}{suffix}"


def money_payload(label: str, value: float | None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"label": label, "value": rounded(value, 2)}
    payload.update({key: val for key, val in extra.items() if val is not None})
    return payload


def pe_caution(row: Comparison) -> dict[str, Any]:
    inputs = row.inputs
    if row.direct_pe is None and row.scs_pe is None:
        short_message = (
            f"{row.ticker} P/E has an open QC caution. The ratio could not be computed or "
            "cross-checked because market cap or selected net profit is missing."
        )
    elif row.direct_pe is None:
        short_message = (
            f"{row.ticker} P/E has an open QC caution. SCS reference is "
            f"{display_value(row.scs_pe, 'x')}, but direct P/E could not be computed from "
            "market cap and selected net profit."
        )
    elif row.scs_pe is None:
        short_message = (
            f"{row.ticker} P/E has an open QC caution. Direct P/E is "
            f"{display_value(row.direct_pe, 'x')}, but no SCS reference was available for cross-checking."
        )
    else:
        short_message = (
            f"{row.ticker} P/E has an open QC caution. Direct P/E is "
            f"{display_value(row.direct_pe, 'x')} versus SCS reference {display_value(row.scs_pe, 'x')}; "
            "the calculation uses market cap divided by selected net income."
        )
    return {
        "ticker": row.ticker,
        "metric": "pe_ratio",
        "status": "red" if row.status == "flag" else "yellow",
        "short_message": short_message,
        "calculation": {
            "formula": "market_cap / net_profit",
            "numerator": money_payload("Market cap", inputs.market_cap, date=inputs.market_date),
            "denominator": money_payload(
                "Selected net profit",
                inputs.net_profit_amount,
                period_end=inputs.net_profit_period_end,
                period_duration=inputs.net_profit_duration,
                section=inputs.net_profit_section,
                unit_type=inputs.net_profit_unit_type,
                scale_label=inputs.net_profit_scale_label,
                reported_value=inputs.net_profit_value,
            ),
            "result": row.direct_pe,
        },
        "reference_check": {
            "source": "SCS",
            "value": row.scs_pe,
            "absolute_difference": row.pe_abs_diff,
            "relative_difference_pct": row.pe_diff_pct,
        },
        "basis_notes": row.notes,
    }


def dividend_yield_caution(row: Comparison) -> dict[str, Any]:
    inputs = row.inputs
    return {
        "ticker": row.ticker,
        "metric": "dividend_yield",
        "status": "red" if row.status == "flag" else "yellow",
        "short_message": (
            f"{row.ticker} dividend yield has an open QC caution. Direct candidate is "
            f"{display_value(row.direct_dividend_yield_pct, '%')} using {row.direct_dividend_yield_basis or 'n/a'}; "
            f"SCS reference is {display_value(row.scs_dividend_yield_pct, '%')}."
        ),
        "calculation": {
            "formula": "dividend_candidate / market_cap",
            "basis": row.direct_dividend_yield_basis,
            "numerator": money_payload(
                "Dividends paid proxy",
                inputs.dividends_paid_amount,
                period_end=inputs.dividends_paid_period_end,
                period_duration=inputs.dividends_paid_duration,
                section=inputs.dividends_paid_section,
                unit_type=inputs.dividends_paid_unit_type,
                reported_value=inputs.dividends_paid_value,
            ),
            "denominator": money_payload("Market cap", inputs.market_cap, date=inputs.market_date),
            "result_pct": row.direct_dividend_yield_pct,
        },
        "reference_check": {
            "source": "SCS",
            "value_pct": row.scs_dividend_yield_pct,
            "absolute_difference_pp": row.dividend_yield_abs_diff_pp,
            "relative_difference_pct": row.dividend_yield_diff_pct,
        },
        "candidates": inputs.dividend_yield_candidates or [],
        "basis_notes": row.notes,
    }


def quality_flag(
    row: Comparison,
    metric: str,
    category: str,
    severity: str,
    affected_table: str,
    statement_type: str | None,
    period_end: str | None,
    period_duration: str | None,
    section: str | None,
    basis: str | None,
    value: float | None,
    message: str,
    caution: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "flag_id": stable_flag_id(row.ticker, metric, category, affected_table, period_end, period_duration, section),
        "ticker": row.ticker,
        "severity": severity,
        "status": "open",
        "category": category,
        "affected_table": affected_table,
        "failed_stage": "16_scs_ratio_qc",
        "origin_stage": "16_scs_ratio_qc",
        "statement_type": statement_type,
        "filing_period": None,
        "period_end": period_end,
        "period_duration": period_duration,
        "section": section,
        "metric": metric,
        "basis": basis,
        "source_file": None,
        "source_pages": None,
        "rule": category,
        "expected": row.scs_pe if category == "pe_reference_check" else row.scs_dividend_yield_pct,
        "got": row.direct_pe if category == "pe_reference_check" else row.direct_dividend_yield_pct,
        "value": value,
        "message": message,
        "recommended_command": f"python3 run.py --chain scs_ratio_qc --ticker {row.ticker}",
        "user_caution_json": caution,
        "generated_at": generated_at,
    }


def comparison_quality_flags(comparisons: list[Comparison], generated_at: str) -> list[dict[str, Any]]:
    pe_user_caution_notes = {"missing_net_profit", "missing_market_cap"}
    severe_pe_multiple_threshold = 3.0
    scs_basis_explained_threshold = 1.5

    def has_severe_pe_difference(row: Comparison) -> bool:
        if row.direct_pe is None or row.scs_pe is None or row.direct_pe <= 0 or row.scs_pe <= 0:
            return False
        ratio = max(row.direct_pe, row.scs_pe) / min(row.direct_pe, row.scs_pe)
        return ratio >= severe_pe_multiple_threshold

    def scs_explained_by_other_reported_candidate(row: Comparison) -> bool:
        if row.scs_pe is None or row.scs_pe <= 0:
            return False
        inputs = row.inputs
        for candidate in inputs.net_profit_candidates or []:
            pe = candidate.get("pe")
            if pe is None or pe <= 0:
                continue
            if not candidate.get("is_reported_scale"):
                continue
            if candidate.get("is_default_row"):
                continue
            ratio = max(float(pe), row.scs_pe) / min(float(pe), row.scs_pe)
            if ratio <= scs_basis_explained_threshold:
                return True
        return False

    # Treat SCS P/E=0 as a non-comparable external reference, not a direct-vs-
    # reference mismatch. Keep it in the operator report, but do not publish a
    # user-facing caution when D1 can compute market_cap / net_profit.
    # Dividend yield remains an operator comparison until the corpus has a
    # declared-dividend/DPS basis. SCS uses declared dividend yield, while D1
    # currently has only a cash-flow dividends-paid proxy plus deprecated legacy
    # market_data.dividend_yield. Publishing those basis differences as user
    # cautions over-flags healthy rows.
    dy_user_caution_notes: set[str] = set()
    rows: list[dict[str, Any]] = []
    for row in comparisons:
        if row.status == "ok":
            continue
        inputs = row.inputs
        notes = set(row.notes)
        publish_pe_caution = bool(notes & pe_user_caution_notes) or (
            has_severe_pe_difference(row) and not scs_explained_by_other_reported_candidate(row)
        )
        if publish_pe_caution:
            targets = []
            if inputs.net_profit_period_end:
                targets.append((
                    "financial_statements",
                    "profit_loss",
                    inputs.net_profit_period_end,
                    inputs.net_profit_duration,
                    inputs.net_profit_section,
                    "net_profit",
                ))
            if inputs.market_date:
                targets.append(("market_data", None, inputs.market_date, None, None, "market_cap"))
            if not targets:
                targets.append(("market_data", None, inputs.market_date, None, None, "market_cap"))
            caution = pe_caution(row)
            for affected_table, statement_type, period_end, period_duration, section, metric in targets:
                rows.append(quality_flag(
                    row=row,
                    metric=metric,
                    category="pe_reference_check",
                    severity="warning",
                    affected_table=affected_table,
                    statement_type=statement_type,
                    period_end=period_end,
                    period_duration=period_duration,
                    section=section,
                    basis=inputs.net_profit_selection_reason,
                    value=row.direct_pe,
                    message=caution["short_message"],
                    caution=caution,
                    generated_at=generated_at,
                ))
        if notes & dy_user_caution_notes:
            targets = []
            if inputs.dividends_paid_period_end:
                targets.append((
                    "financial_statements",
                    "cash_flow",
                    inputs.dividends_paid_period_end,
                    inputs.dividends_paid_duration,
                    inputs.dividends_paid_section,
                    "dividends_paid",
                ))
            if inputs.market_date:
                targets.append(("market_data", None, inputs.market_date, None, None, "dividend_yield"))
            if not targets:
                targets.append(("market_data", None, inputs.market_date, None, None, "dividend_yield"))
            caution = dividend_yield_caution(row)
            for affected_table, statement_type, period_end, period_duration, section, metric in targets:
                rows.append(quality_flag(
                    row=row,
                    metric=metric,
                    category="dividend_yield_reference_check",
                    severity="warning",
                    affected_table=affected_table,
                    statement_type=statement_type,
                    period_end=period_end,
                    period_duration=period_duration,
                    section=section,
                    basis=row.direct_dividend_yield_basis,
                    value=row.direct_dividend_yield_pct,
                    message=caution["short_message"],
                    caution=caution,
                    generated_at=generated_at,
                ))
    return rows


def write_quality_flags(path: Path, flags: list[dict[str, Any]]) -> None:
    with path.open("w") as handle:
        for flag in flags:
            handle.write(json.dumps(flag, separators=(",", ":")) + "\n")


def http_post_json(url: str, payload: dict[str, str], referer: str, retries: int = 2) -> dict[str, Any]:
    body = json.dumps(payload)
    for attempt in range(retries + 1):
        result = subprocess.run(
            [
                "curl", "-fsSL", "--max-time", "25",
                "-A", "Mozilla/5.0",
                "-H", "Content-Type: application/json; charset=UTF-8",
                "-H", "X-Requested-With: XMLHttpRequest",
                "-H", "Origin: https://www.scstrade.com",
                "-H", f"Referer: {referer}",
                "--data", body,
                url,
            ],
            text=True,
            capture_output=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(result.stderr[-1000:] or f"SCS request failed for {payload}")


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).replace(",", "").replace("Rs.", "").replace("%", "").replace("x", "").strip()
    if not text or text.upper() in {"N/A", "NA", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def row_float(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in row:
            parsed = as_float(row.get(key))
            if parsed is not None:
                return parsed
    return None


def fetch_scs_references() -> dict[str, ScsReference]:
    refs: dict[str, ScsReference] = {}
    for source, meta in SCS_PAGES.items():
        payload = http_post_json(SCS_ENDPOINT, {"id": meta["id"]}, meta["url"])
        inner = json.loads(payload.get("d") or "{}")
        for row in inner.get("dt") or []:
            ticker = str(row.get("Symbol") or "").strip().upper()
            if not ticker:
                continue
            current = refs.get(ticker)
            if not current:
                current = ScsReference(
                    ticker=ticker,
                    name=str(row.get("Name") or "").strip() or None,
                    source=source,
                    scs_price=row_float(row, "Price"),
                    scs_pe=row_float(row, "P_/_E"),
                    scs_dividend_yield_pct=row_float(row, "Dividend_Yield", "Dividend Yield"),
                    scs_eps_annual=row_float(row, "EPS_Annual"),
                    scs_eps_latest=str(row.get("EPS_Latest") or "").strip() or None,
                )
                refs[ticker] = current
            else:
                sources = set(current.source.split(","))
                sources.add(source)
                current.source = ",".join(sorted(sources))
                current.scs_price = current.scs_price if current.scs_price is not None else row_float(row, "Price")
                current.scs_pe = current.scs_pe if current.scs_pe is not None else row_float(row, "P_/_E")
                current.scs_dividend_yield_pct = (
                    current.scs_dividend_yield_pct
                    if current.scs_dividend_yield_pct is not None
                    else row_float(row, "Dividend_Yield", "Dividend Yield")
                )
    return refs


def fetch_d1_tickers(include_market_only: bool = False) -> list[str]:
    market_union = "\n  UNION\n  SELECT DISTINCT ticker FROM market_data" if include_market_only else ""
    rows = d1_query(f"""
SELECT ticker
FROM (
  SELECT DISTINCT ticker FROM financial_statements
  UNION
  SELECT DISTINCT ticker FROM ratios
  {market_union}
)
WHERE ticker IS NOT NULL AND ticker != ''
ORDER BY ticker;
""")
    return [str(row["ticker"]).upper() for row in rows]


def d1_query(sql: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", "--json", "--command", sql],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] if result.stderr else "wrangler d1 query failed")
    payload = json.loads(result.stdout)
    rows: list[dict[str, Any]] = []
    for block in payload if isinstance(payload, list) else []:
        if isinstance(block, dict) and isinstance(block.get("results"), list):
            rows.extend(block["results"])
    return rows


def sql_list(values: list[str]) -> str:
    return ",".join("'" + v.replace("'", "''") + "'" for v in values)


def unit_scale(unit_type: str | None) -> float:
    return UNIT_SCALE.get((unit_type or "thousands").strip().lower(), 1_000.0)


def pick_default_net_profit_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [row for row in rows if row.get("statement_type") == "profit_loss"]
    if not candidates:
        return None

    def method_rank(row: dict[str, Any]) -> int:
        method = str(row.get("method") or "")
        if method == "direct":
            return 4
        if method.startswith("LTM_9M"):
            return 3
        if method.startswith("LTM_6M"):
            return 2
        if method.startswith("LTM_3M"):
            return 1
        return 0

    def score(row: dict[str, Any]) -> tuple[str, int, int, int]:
        duration = str(row.get("period_duration") or "")
        section = str(row.get("section") or "")
        return (
            str(row.get("period_end") or ""),
            2 if section == "consolidated" else 1 if section == "unconsolidated" else 0,
            2 if duration == "12M" else 1 if duration == "LTM" else 0,
            method_rank(row),
        )

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def pick_default_statement_row(rows: list[dict[str, Any]], statement_type: str) -> dict[str, Any] | None:
    candidates = [row for row in rows if row.get("statement_type") == statement_type]
    if not candidates:
        return None

    def score(row: dict[str, Any]) -> tuple[int, str, int]:
        duration = str(row.get("period_duration") or "")
        section = str(row.get("section") or "")
        return (
            2 if duration == "12M" else 1 if duration == "LTM" else 0,
            str(row.get("period_end") or ""),
            2 if section == "consolidated" else 1 if section == "unconsolidated" else 0,
        )

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def candidate_scales(unit_type: str | None) -> list[tuple[str, float, bool]]:
    reported = unit_scale(unit_type)
    scales = [("reported", reported, True)]
    normalized = (unit_type or "thousands").strip().lower()
    if normalized in {"thousands", "thousand"}:
        scales.append(("millions_override", 1_000_000.0, False))
    elif normalized in {"million", "millions"}:
        scales.append(("thousands_override", 1_000.0, False))
    return scales


def net_profit_candidates(rows: list[dict[str, Any]], market_cap: float | None) -> list[dict[str, Any]]:
    defaults = pick_default_net_profit_row(rows)

    def method_rank(row: dict[str, Any]) -> int:
        method = str(row.get("method") or "")
        if method == "direct":
            return 4
        if method.startswith("LTM_9M"):
            return 3
        if method.startswith("LTM_6M"):
            return 2
        if method.startswith("LTM_3M"):
            return 1
        return 0

    candidates: list[dict[str, Any]] = []
    for row in rows:
        value = as_float(row.get("value"))
        if value is None:
            continue
        for scale_label, scale, is_reported_scale in candidate_scales(row.get("unit_type")):
            amount = value * scale
            pe = market_cap / amount if market_cap and market_cap > 0 and amount > 0 else None
            candidates.append({
                "period_end": row.get("period_end"),
                "period_duration": row.get("period_duration"),
                "section": row.get("section"),
                "method": row.get("method"),
                "unit_type": row.get("unit_type"),
                "value": value,
                "scale_label": scale_label,
                "scale": scale,
                "amount": amount,
                "pe": round(pe, 4) if pe is not None else None,
                "is_reported_scale": is_reported_scale,
                "is_default_row": bool(
                    defaults
                    and row.get("period_end") == defaults.get("period_end")
                    and row.get("period_duration") == defaults.get("period_duration")
                    and row.get("section") == defaults.get("section")
                    and row.get("method") == defaults.get("method")
                ),
            })
    candidates.sort(
        key=lambda row: (
            0 if row["is_reported_scale"] else 1,
            0 if row["is_default_row"] else 1,
            -method_rank(row),
            str(row["period_end"] or ""),
        ),
        reverse=False,
    )
    return candidates


def select_net_profit_candidate(
    candidates: list[dict[str, Any]],
    scs_pe: float | None,
    pe_abs_threshold: float,
    pe_pct_threshold: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    default = next((row for row in candidates if row.get("is_default_row") and row.get("is_reported_scale")), None)
    comparable = [row for row in candidates if row.get("pe") is not None]
    if default:
        return default, default, "default_latest_annual_or_ltm"
    return (comparable[0], default, "first_comparable_net_profit") if comparable else (None, default, None)


def fetch_direct_inputs(tickers: list[str]) -> dict[str, DirectInputs]:
    inputs = {ticker: DirectInputs(ticker=ticker) for ticker in tickers}
    if not tickers:
        return inputs
    for i in range(0, len(tickers), 80):
        chunk = tickers[i:i + 80]
        rows = d1_query(f"""
WITH latest_market AS (
  SELECT ticker, date, closing_price, total_shares, market_cap, dividend_yield,
         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
  FROM market_data
  WHERE ticker IN ({sql_list(chunk)})
)
SELECT ticker, date, closing_price, total_shares, market_cap, dividend_yield
FROM latest_market
WHERE rn = 1;
""")
        for row in rows:
            target = inputs[str(row["ticker"]).upper()]
            target.market_date = row.get("date")
            target.closing_price = as_float(row.get("closing_price"))
            target.market_cap = as_float(row.get("market_cap"))
            target.shares = as_float(row.get("total_shares"))
            target.market_dividend_yield_pct = as_float(row.get("dividend_yield"))

        fs_rows = d1_query(f"""
SELECT ticker, period_end, period_duration, section, statement_type, canonical_name, method, unit_type, value
FROM financial_statements
WHERE ticker IN ({sql_list(chunk)})
  AND statement_type IN ('profit_loss','cash_flow')
  AND canonical_name IN ('net_profit','dividends_paid')
  AND period_duration IN ('12M','LTM')
  AND section IN ('unconsolidated','consolidated')
  AND value IS NOT NULL
  AND COALESCE(qc_flag, '') = ''
ORDER BY ticker, period_end DESC, period_duration DESC, section DESC;
""")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in fs_rows:
            grouped.setdefault(str(row["ticker"]).upper(), []).append(row)
        for ticker, ticker_rows in grouped.items():
            target = inputs[ticker]
            net_rows = [row for row in ticker_rows if row.get("canonical_name") == "net_profit"]
            candidates = net_profit_candidates(net_rows, target.market_cap)
            selected, default, selection_reason = select_net_profit_candidate(candidates, None, 2.0, 25.0)
            target.net_profit_candidates = candidates
            target.net_profit_selection_reason = selection_reason
            if default:
                target.default_net_profit_period_end = default.get("period_end")
                target.default_net_profit_duration = default.get("period_duration")
                target.default_net_profit_section = default.get("section")
                target.default_net_profit_unit_type = default.get("unit_type")
                target.default_net_profit_value = default.get("value")
                target.default_net_profit_amount = default.get("amount")
                target.default_net_profit_pe = default.get("pe")
            net = selected
            if net:
                target.net_profit_period_end = net.get("period_end")
                target.net_profit_duration = net.get("period_duration")
                target.net_profit_section = net.get("section")
                target.net_profit_unit_type = net.get("unit_type")
                target.net_profit_value = net.get("value")
                target.net_profit_scale_label = net.get("scale_label")
                target.net_profit_amount = net.get("amount")
            div = pick_default_statement_row(
                [row for row in ticker_rows if row.get("canonical_name") == "dividends_paid"],
                "cash_flow",
            )
            if div:
                target.dividends_paid_period_end = div.get("period_end")
                target.dividends_paid_duration = div.get("period_duration")
                target.dividends_paid_section = div.get("section")
                target.dividends_paid_unit_type = div.get("unit_type")
                target.dividends_paid_value = as_float(div.get("value"))
                if target.dividends_paid_value is not None:
                    target.dividends_paid_amount = abs(target.dividends_paid_value) * unit_scale(target.dividends_paid_unit_type)
                    if target.shares and target.shares > 0:
                        target.dividends_paid_per_share = target.dividends_paid_amount / target.shares
    return inputs


def pct_diff(reference: float | None, actual: float | None) -> float | None:
    if reference is None or actual is None or reference == 0:
        return None
    return abs(actual - reference) / abs(reference) * 100


def select_dividend_yield_candidate(
    direct: DirectInputs,
    scs_dividend_yield_pct: float | None,
) -> tuple[float | None, str | None, list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    if direct.market_dividend_yield_pct is not None:
        candidates.append({
            "basis": "market_data_dividend_yield_legacy",
            "value": direct.market_dividend_yield_pct,
            "is_legacy": True,
            "is_implausible": direct.market_dividend_yield_pct < 0 or direct.market_dividend_yield_pct > 100,
        })
    if direct.dividends_paid_amount is not None and direct.market_cap and direct.market_cap > 0:
        candidates.append({
            "basis": "cash_flow_dividends_paid_proxy",
            "value": direct.dividends_paid_amount / direct.market_cap * 100,
            "is_legacy": False,
            "is_implausible": False,
        })
    if not candidates:
        return None, None, candidates
    usable = [row for row in candidates if not row["is_implausible"] and not row["is_legacy"]]
    if scs_dividend_yield_pct is not None:
        selected = min(usable, key=lambda row: abs(float(row["value"]) - scs_dividend_yield_pct)) if usable else None
    else:
        selected = usable[0] if usable else None
    if selected is None:
        return None, None, candidates
    return float(selected["value"]), str(selected["basis"]), candidates


def compare_one(
    ref: ScsReference,
    direct: DirectInputs,
    pe_abs_threshold: float,
    pe_pct_threshold: float,
    dy_abs_threshold: float,
    dy_pct_threshold: float,
) -> Comparison:
    notes: list[str] = []
    if not direct.market_cap or direct.market_cap <= 0:
        notes.append("missing_market_cap")

    selected, default, selection_reason = select_net_profit_candidate(
        direct.net_profit_candidates or [],
        ref.scs_pe,
        pe_abs_threshold,
        pe_pct_threshold,
    )
    direct.net_profit_selection_reason = selection_reason
    if default:
        direct.default_net_profit_period_end = default.get("period_end")
        direct.default_net_profit_duration = default.get("period_duration")
        direct.default_net_profit_section = default.get("section")
        direct.default_net_profit_unit_type = default.get("unit_type")
        direct.default_net_profit_value = default.get("value")
        direct.default_net_profit_amount = default.get("amount")
        direct.default_net_profit_pe = default.get("pe")
    if selected:
        direct.net_profit_period_end = selected.get("period_end")
        direct.net_profit_duration = selected.get("period_duration")
        direct.net_profit_section = selected.get("section")
        direct.net_profit_unit_type = selected.get("unit_type")
        direct.net_profit_value = selected.get("value")
        direct.net_profit_scale_label = selected.get("scale_label")
        direct.net_profit_amount = selected.get("amount")

    direct_pe: float | None = selected.get("pe") if selected else None
    if direct.net_profit_amount is None:
        notes.append("missing_net_profit")
    elif direct.net_profit_amount <= 0:
        notes.append("non_positive_net_profit")
    if selected and not selected.get("is_reported_scale"):
        notes.append("net_profit_unit_scale_override_candidate")
    if selected and default:
        if selected.get("period_end") != default.get("period_end"):
            notes.append("scs_matches_different_net_profit_period")
        if selected.get("section") != default.get("section"):
            notes.append("scs_matches_different_net_profit_section")
        if selected.get("period_duration") != default.get("period_duration"):
            notes.append("scs_matches_different_net_profit_duration")

    scs_implied_dps = (
        ref.scs_price * ref.scs_dividend_yield_pct / 100
        if ref.scs_price is not None and ref.scs_dividend_yield_pct is not None
        else None
    )
    direct_dy, direct_dy_basis, dy_candidates = select_dividend_yield_candidate(
        direct,
        ref.scs_dividend_yield_pct,
    )
    direct.dividend_yield_candidates = dy_candidates
    dividends_paid_yield = next(
        (float(row["value"]) for row in dy_candidates if row["basis"] == "cash_flow_dividends_paid_proxy"),
        None,
    )
    market_dy = next(
        (float(row["value"]) for row in dy_candidates if row["basis"] == "market_data_dividend_yield_legacy"),
        None,
    )
    if market_dy is not None:
        notes.append("market_data_dividend_yield_is_legacy")
    if any(row.get("is_implausible") for row in dy_candidates):
        notes.append("market_data_dividend_yield_implausible")
    if direct_dy_basis == "cash_flow_dividends_paid_proxy":
        notes.append("dividend_yield_uses_cashflow_proxy")

    close_diff = pct_diff(ref.scs_price, direct.closing_price)
    pe_abs = abs(direct_pe - ref.scs_pe) if direct_pe is not None and ref.scs_pe is not None else None
    pe_rel = pct_diff(ref.scs_pe, direct_pe)
    dy_abs = abs(direct_dy - ref.scs_dividend_yield_pct) if direct_dy is not None and ref.scs_dividend_yield_pct is not None else None
    dy_rel = pct_diff(ref.scs_dividend_yield_pct, direct_dy)

    if pe_abs is not None:
        if ref.scs_pe == 0 and direct_pe and direct_pe > pe_abs_threshold:
            notes.append("pe_scs_zero_direct_positive")
        elif pe_abs > pe_abs_threshold and (pe_rel is None or pe_rel > pe_pct_threshold):
            notes.append("pe_diff_above_threshold")
    if dy_abs is not None:
        if ref.scs_dividend_yield_pct == 0 and direct_dy and direct_dy > dy_abs_threshold:
            notes.append("dividend_yield_scs_zero_direct_positive")
        elif dy_abs > dy_abs_threshold and (dy_rel is None or dy_rel > dy_pct_threshold):
            notes.append("dividend_yield_diff_above_threshold")
    if ref.scs_dividend_yield_pct is not None and ref.scs_dividend_yield_pct > 0 and direct_dy is None:
        notes.append("missing_direct_dividend_yield")

    blocking_notes = {
        "missing_market_cap",
        "pe_scs_zero_direct_positive",
        "pe_diff_above_threshold",
        "dividend_yield_scs_zero_direct_positive",
        "dividend_yield_diff_above_threshold",
        "missing_direct_dividend_yield",
    }
    if direct_pe is None and ref.scs_pe is not None and ref.scs_pe > 0:
        blocking_notes.add("missing_comparable_pe")
        notes.append("missing_comparable_pe")
    warning_notes = {
        "net_profit_unit_scale_override_candidate",
        "scs_matches_different_net_profit_period",
        "scs_matches_different_net_profit_section",
        "scs_matches_different_net_profit_duration",
        "market_data_dividend_yield_implausible",
    }
    if any(note in blocking_notes for note in notes):
        status = "flag"
    elif any(note in warning_notes for note in notes):
        status = "warning"
    else:
        status = "ok"
    return Comparison(
        ticker=ref.ticker,
        name=ref.name,
        sources=ref.source.split(","),
        status=status,
        notes=notes,
        scs_price=ref.scs_price,
        d1_closing_price=direct.closing_price,
        close_diff_pct=round(close_diff, 4) if close_diff is not None else None,
        scs_pe=ref.scs_pe,
        direct_pe=round(direct_pe, 4) if direct_pe is not None else None,
        pe_abs_diff=round(pe_abs, 4) if pe_abs is not None else None,
        pe_diff_pct=round(pe_rel, 4) if pe_rel is not None else None,
        scs_dividend_yield_pct=ref.scs_dividend_yield_pct,
        direct_dividend_yield_pct=round(direct_dy, 4) if direct_dy is not None else None,
        direct_dividend_yield_basis=direct_dy_basis,
        scs_implied_dividend_per_share=round(scs_implied_dps, 4) if scs_implied_dps is not None else None,
        dividends_paid_yield_pct=round(dividends_paid_yield, 4) if dividends_paid_yield is not None else None,
        dividend_yield_abs_diff_pp=round(dy_abs, 4) if dy_abs is not None else None,
        dividend_yield_diff_pct=round(dy_rel, 4) if dy_rel is not None else None,
        inputs=direct,
    )


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    flagged = [row for row in report["comparisons"] if row["status"] == "flag"]
    warnings = [row for row in report["comparisons"] if row["status"] == "warning"]

    def pe_notes(row: dict[str, Any]) -> str:
        return ", ".join(row["notes"]) if row["notes"] else "none"

    lines = [
        "# Market-Cap / Net-Income and Dividend-Yield QC",
        "",
        f"- Run at: `{report['run_at']}`",
        f"- D1 ticker universe: `{report['total_d1_tickers']}`",
        f"- SCS reference rows available: `{report['total_references']}`",
        f"- Tickers with SCS reference: `{report['scs_reference_tickers']}`",
        f"- SCS P/E reference tickers: `{report['scs_pe_reference_tickers']}`",
        f"- SCS dividend-yield reference tickers: `{report['scs_dividend_yield_reference_tickers']}`",
        f"- Direct P/E available tickers: `{report['direct_pe_available_tickers']}`",
        f"- Direct dividend-yield available tickers: `{report['direct_dividend_yield_available_tickers']}`",
        f"- D1 tickers without SCS reference: `{report['d1_tickers_without_scs_reference']}`",
        f"- SCS reference tickers not in D1: `{report['scs_reference_tickers_not_in_d1']}`",
        f"- Compared rows: `{report['total_compared']}`",
        f"- Flags: `{report['flags']}`",
        f"- Warnings: `{report['warnings']}`",
        f"- P/E issue tickers: `{report['pe_issue_tickers']}`",
        f"- Dividend-yield issue tickers: `{report['dividend_yield_issue_tickers']}`",
        f"- P/E thresholds: abs `{report['thresholds']['pe_abs']}`, relative `{report['thresholds']['pe_pct']}%`",
        f"- Dividend yield thresholds: abs `{report['thresholds']['dividend_yield_abs_pp']}pp`, relative `{report['thresholds']['dividend_yield_pct']}%`",
        "",
        "The default run covers the served D1 statement/ratio corpus. Pass --include-market-only only when intentionally auditing legacy market_data-only symbols outside the served health matrix.",
        "SCS is an external reference subset only; PSX/D1 remains authoritative for close and market cap.",
        "P/E is computed as latest D1 market cap divided by a selected annual/LTM net_profit candidate.",
        "When SCS only matches a different period, section, or unit scale, this report flags that basis explicitly instead of reconstructing historical shares.",
        "Dividend yield is compared against labeled candidates: legacy D1 market_data.dividend_yield and cash_flow.dividends_paid / market_cap.",
        "",
        "## Flags",
        "",
    ]
    if not flagged:
        lines.append("No flags.")
    else:
        lines.append("| Ticker | Notes | SCS P/E | Direct P/E | SCS DY % | Direct DY % | DY Basis | DY Diff pp | Net Income Basis | Default Basis |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | --- | --- |")
        for row in flagged:
            inputs = row["inputs"]
            basis = (
                f"{inputs.get('net_profit_section')}/{inputs.get('net_profit_duration')}"
                f" {inputs.get('net_profit_period_end')}, "
                f"{inputs.get('net_profit_unit_type')}:{inputs.get('net_profit_scale_label')}, "
                f"amount {inputs.get('net_profit_amount')}"
            )
            default_basis = (
                f"{inputs.get('default_net_profit_section')}/{inputs.get('default_net_profit_duration')}"
                f" {inputs.get('default_net_profit_period_end')}, "
                f"amount {inputs.get('default_net_profit_amount')}, "
                f"P/E {inputs.get('default_net_profit_pe')}"
            )
            lines.append(
                f"| {row['ticker']} | {pe_notes(row)} | "
                f"{row.get('scs_pe')} | {row.get('direct_pe')} | "
                f"{row.get('scs_dividend_yield_pct')} | {row.get('direct_dividend_yield_pct')} | "
                f"{row.get('direct_dividend_yield_basis')} | {row.get('dividend_yield_abs_diff_pp')} | "
                f"{basis} | {default_basis} |"
            )
    lines.extend(["", "## Warnings", ""])
    if not warnings:
        lines.append("No warnings.")
    else:
        lines.append("| Ticker | Notes | SCS P/E | Direct P/E | SCS DY % | Direct DY % | DY Basis | DY Diff pp | Net Income Basis | Default Basis |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | --- | --- |")
        for row in warnings:
            inputs = row["inputs"]
            basis = (
                f"{inputs.get('net_profit_section')}/{inputs.get('net_profit_duration')}"
                f" {inputs.get('net_profit_period_end')}, "
                f"{inputs.get('net_profit_unit_type')}:{inputs.get('net_profit_scale_label')}, "
                f"amount {inputs.get('net_profit_amount')}"
            )
            default_basis = (
                f"{inputs.get('default_net_profit_section')}/{inputs.get('default_net_profit_duration')}"
                f" {inputs.get('default_net_profit_period_end')}, "
                f"amount {inputs.get('default_net_profit_amount')}, "
                f"P/E {inputs.get('default_net_profit_pe')}"
            )
            lines.append(
                f"| {row['ticker']} | {pe_notes(row)} | "
                f"{row.get('scs_pe')} | {row.get('direct_pe')} | "
                f"{row.get('scs_dividend_yield_pct')} | {row.get('direct_dividend_yield_pct')} | "
                f"{row.get('direct_dividend_yield_basis')} | {row.get('dividend_yield_abs_diff_pp')} | "
                f"{basis} | {default_basis} |"
            )
    path.write_text("\n".join(lines) + "\n")


def write_csv(path: Path, comparisons: list[Comparison]) -> None:
    rows = [asdict(row) for row in comparisons]
    fieldnames = [
        "ticker", "name", "sources", "status", "notes", "scs_price", "d1_closing_price",
        "close_diff_pct", "scs_pe", "direct_pe", "pe_abs_diff", "pe_diff_pct",
        "scs_dividend_yield_pct", "direct_dividend_yield_pct", "direct_dividend_yield_basis",
        "dividend_yield_abs_diff_pp", "dividend_yield_diff_pct",
        "scs_implied_dividend_per_share", "dividends_paid_yield_pct",
        "net_profit_period_end", "net_profit_duration", "net_profit_section",
        "net_profit_unit_type", "net_profit_scale_label", "net_profit_value",
        "net_profit_amount", "net_profit_selection_reason",
        "default_net_profit_period_end", "default_net_profit_duration",
        "default_net_profit_section", "default_net_profit_unit_type",
        "default_net_profit_value", "default_net_profit_amount", "default_net_profit_pe",
        "market_dividend_yield_pct",
        "dividends_paid_period_end", "dividends_paid_duration", "dividends_paid_section",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            inputs = row.pop("inputs")
            row["sources"] = ",".join(row["sources"])
            row["notes"] = ",".join(row["notes"])
            for key in [
                "net_profit_period_end", "net_profit_duration", "net_profit_section",
                "net_profit_unit_type", "net_profit_scale_label", "net_profit_value",
                "net_profit_amount", "net_profit_selection_reason",
                "default_net_profit_period_end", "default_net_profit_duration",
                "default_net_profit_section", "default_net_profit_unit_type",
                "default_net_profit_value", "default_net_profit_amount", "default_net_profit_pe",
                "market_dividend_yield_pct",
                "dividends_paid_period_end", "dividends_paid_duration", "dividends_paid_section",
            ]:
                row[key] = inputs.get(key)
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 16 SCS ratio QC — P/E and dividend yield")
    parser.add_argument("--ticker", help="Single ticker to compare")
    parser.add_argument("--limit", type=int, help="Limit ticker rows for rehearsal")
    parser.add_argument("--scs-only", action="store_true", help="Only compare tickers that appear in SCS reference rows")
    parser.add_argument("--include-market-only", action="store_true", help="Also compare D1 market_data-only symbols outside the served statement/ratio corpus")
    parser.add_argument("--pe-abs-threshold", type=float, default=2.0)
    parser.add_argument("--pe-pct-threshold", type=float, default=25.0)
    parser.add_argument("--dividend-yield-abs-threshold", type=float, default=2.0)
    parser.add_argument("--dividend-yield-pct-threshold", type=float, default=25.0)
    args = parser.parse_args()

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    scs_refs = fetch_scs_references()
    d1_tickers = fetch_d1_tickers(include_market_only=args.include_market_only)
    tickers = sorted(scs_refs) if args.scs_only else sorted(d1_tickers)
    if args.ticker:
        ticker = args.ticker.upper()
        tickers = [value for value in tickers if value == ticker]
        if not tickers:
            tickers = [ticker]
    if args.limit:
        tickers = tickers[:args.limit]

    refs: dict[str, ScsReference] = {}
    for ticker in tickers:
        refs[ticker] = scs_refs.get(ticker) or ScsReference(
            ticker=ticker,
            name=None,
            source="d1_universe",
            scs_price=None,
            scs_pe=None,
            scs_dividend_yield_pct=None,
            scs_eps_annual=None,
            scs_eps_latest=None,
        )
    direct_inputs = fetch_direct_inputs(tickers)
    comparisons = [
        compare_one(
            refs[ticker],
            direct_inputs[ticker],
            args.pe_abs_threshold,
            args.pe_pct_threshold,
            args.dividend_yield_abs_threshold,
            args.dividend_yield_pct_threshold,
        )
        for ticker in tickers
    ]
    comparisons.sort(key=lambda row: ({"flag": 0, "warning": 1, "ok": 2}.get(row.status, 3), row.ticker))
    flags = [row for row in comparisons if row.status == "flag"]
    warnings = [row for row in comparisons if row.status == "warning"]
    note_counts: dict[str, int] = {}
    for row in comparisons:
        for note in row.notes:
            note_counts[note] = note_counts.get(note, 0) + 1
    status_counts: dict[str, int] = {}
    for row in comparisons:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
    dy_notes = {
        "missing_direct_dividend_yield",
        "dividend_yield_diff_above_threshold",
        "dividend_yield_scs_zero_direct_positive",
    }
    pe_notes = {
        "missing_comparable_pe",
        "pe_diff_above_threshold",
        "pe_scs_zero_direct_positive",
        "missing_market_cap",
    }

    run_at = datetime.now(timezone.utc).isoformat()
    report = {
        "run_at": run_at,
        "sources": SCS_PAGES,
        "thresholds": {
            "pe_abs": args.pe_abs_threshold,
            "pe_pct": args.pe_pct_threshold,
            "dividend_yield_abs_pp": args.dividend_yield_abs_threshold,
            "dividend_yield_pct": args.dividend_yield_pct_threshold,
        },
        "include_market_only": args.include_market_only,
        "total_d1_tickers": len(d1_tickers),
        "total_references": len(scs_refs),
        "scs_reference_tickers": len([ticker for ticker in tickers if ticker in scs_refs]),
        "scs_pe_reference_tickers": len([row for row in comparisons if row.scs_pe is not None]),
        "scs_dividend_yield_reference_tickers": len([
            row for row in comparisons if row.scs_dividend_yield_pct is not None
        ]),
        "direct_pe_available_tickers": len([row for row in comparisons if row.direct_pe is not None]),
        "direct_dividend_yield_available_tickers": len([
            row for row in comparisons if row.direct_dividend_yield_pct is not None
        ]),
        "pe_issue_tickers": len([row for row in comparisons if any(note in pe_notes for note in row.notes)]),
        "dividend_yield_issue_tickers": len([
            row for row in comparisons if any(note in dy_notes for note in row.notes)
        ]),
        "d1_tickers_without_scs_reference": len([ticker for ticker in d1_tickers if ticker not in scs_refs]),
        "scs_reference_tickers_not_in_d1": len([ticker for ticker in scs_refs if ticker not in set(d1_tickers)]),
        "scs_reference_not_in_d1_tickers": [ticker for ticker in sorted(scs_refs) if ticker not in set(d1_tickers)],
        "total_compared": len(comparisons),
        "flags": len(flags),
        "warnings": len(warnings),
        "status_counts": dict(sorted(status_counts.items())),
        "note_counts": dict(sorted(note_counts.items())),
        "flagged_tickers": [row.ticker for row in flags],
        "warning_tickers": [row.ticker for row in warnings],
        "comparisons": [asdict(row) for row in comparisons],
    }
    report_path = ARTIFACTS_DIR / "scs_ratio_qc.json"
    csv_path = ARTIFACTS_DIR / "scs_ratio_qc.csv"
    md_path = ARTIFACTS_DIR / "scs_ratio_qc.md"
    quality_flags = comparison_quality_flags(comparisons, run_at)
    report_path.write_text(json.dumps(report, indent=2))
    write_csv(csv_path, comparisons)
    write_markdown(md_path, report)
    write_quality_flags(QUALITY_FLAGS_PATH, quality_flags)
    write_report("16_scs_ratio_qc", report)

    print(f"Market-cap / net-income and dividend-yield QC complete: {len(flags)} flags, {len(warnings)} warnings / {len(comparisons)} tickers compared")
    print(f"Report -> {report_path}")
    print(f"Markdown -> {md_path}")
    print(f"Quality flags -> {QUALITY_FLAGS_PATH} ({len(quality_flags)} rows)")
    emit_decision(
        "16_scs_ratio_qc",
        f"Compared {len(comparisons):,} tickers; {len(flags):,} ratio flags and {len(warnings):,} warnings.",
    )
    return 1 if flags else 0


if __name__ == "__main__":
    raise SystemExit(main())
