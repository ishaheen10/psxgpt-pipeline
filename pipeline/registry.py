"""Statement registry — the per-statement config that drives the single extractor.

Extraction is identical across statements except for: which statement key to pull from
the page map, the canonical-schema field, the prompt pack, output/checkpoint paths, the
per-company-type domain notes, and whether quarter-mapping context applies (flow
statements yes, balance sheet no). All of that is data, kept here, so there is ONE
extractor (extract.py) instead of three near-identical scripts.
"""
from __future__ import annotations

# Per-(statement, company_type) domain notes injected as {type_note}. These encode
# statement-specific sign conventions / field guidance that the packs reference.
_PL_BANK = """
Key bank fields: bank_interest_income, bank_interest_expense -> net_interest_income (instead of gross_profit)
Non-interest income: fee_income, fx_income, dividend_income, trading_gains
Expenses: operating_expenses, provisions

PROVISIONS AND REVERSALS: Banks show provisions with REVERSED parentheses convention.
- When the source shows provisions IN PARENTHESES like (2,710,139) → this is a REVERSAL/CREDIT → store as POSITIVE (adds to profit)
- When the source shows provisions WITHOUT parentheses like 2,976,973 → this is an EXPENSE → store as NEGATIVE (reduces profit)
- This is OPPOSITE of the normal parentheses convention - for bank provisions, parentheses = good (reversal), no parentheses = bad (expense)"""

_PL_INSURANCE = """
Key insurance fields: gross_premium, net_premium, claims_expense -> underwriting_profit (instead of gross_profit)
Other income: investment_income

CRITICAL SIGN CONVENTION FOR INSURANCE:
- ALL expense items must be NEGATIVE (in parentheses): claims_expense, commission_expense, operating_expenses, acquisition_expenses, other_underwriting
- Reinsurance RECOVERIES are POSITIVE (they reduce the expense/add back money)
- Net claims = Claims (negative) + Recoveries (positive) = net negative
- Example: If "Insurance benefits" is 479,719 in source, store as (479,719) because it's an expense
- Example: If "Reinsurance recoveries" is 331,635 in source, store as 331,635 (positive, it's a recovery)

INSURANCE P&L STRUCTURE:
Insurance P&Ls have TWO separate expense sections, each with its own subtotal:
1. Claims section → ends with "Net claims" or "Net insurance benefits" subtotal
2. Operating expenses section → ends with "Total expenses" subtotal

These are SEPARATE subtotals. "Total expenses" does NOT include the claims subtotal.
The formula for each subtotal should only include items within that section.

HIERARCHICAL SUBTOTALS: Insurance P&Ls often show subtotals that already include prior line items.
- If "Net claims" = "Claims incurred" + "Reinsurance recoveries", don't include all three in formulas
- Use the subtotal OR the components, not both (to avoid double-counting)"""

_BS_BANK = "\nNOTE: Banks do NOT have typical current/non-current asset splits. Use bank-specific canonical fields."
_BS_INSURANCE = "\nNOTE: Insurance companies have industry-specific items. Use insurance-specific canonical fields."
_CF_BANK = """
Bank cash flows typically have multiple intermediate subtotals before reaching cfo/cfi/cff.
Extract ALL subtotals shown in the source - the structure varies by bank."""


STATEMENTS: dict[str, dict] = {
    "pl": {
        "stmt_key": "PL", "schema_field": "profit_loss", "pack": "08_pl",
        "output_dir": "data/extracted_pl", "checkpoint": "artifacts/08_extract/checkpoint_pl.json",
        "quarter_mapping": True,
        "notes": {"BANK": _PL_BANK, "INSURANCE": _PL_INSURANCE, "CORPORATE": ""},
    },
    "bs": {
        "stmt_key": "BS", "schema_field": "balance_sheet", "pack": "08_bs",
        "output_dir": "data/extracted_bs", "checkpoint": "artifacts/08_extract/checkpoint_bs.json",
        "quarter_mapping": False,
        "notes": {"BANK": _BS_BANK, "INSURANCE": _BS_INSURANCE, "CORPORATE": ""},
    },
    "cf": {
        "stmt_key": "CF", "schema_field": "cash_flow", "pack": "08_cf",
        "output_dir": "data/extracted_cf", "checkpoint": "artifacts/08_extract/checkpoint_cf.json",
        "quarter_mapping": True,
        "notes": {"BANK": _CF_BANK, "INSURANCE": "", "CORPORATE": ""},
    },
}
