Extract the {section_label} Cash Flow Statement from these PSX filing pages.

TICKER: {ticker}
PERIOD: {period}
SECTION: {section_label}
COMPANY TYPE: {company_type}
FISCAL YEAR END: {fy_month_name} ({fiscal_period})
{type_note}

<!-- CANONICAL_SECTIONS -->

## CANONICAL FIELDS

{', '.join(cf_fields)}

**IMPORTANT**: Do NOT force a match to these fields. If a line item doesn't clearly match any canonical field above, create an appropriate snake_case name that accurately describes it. Examples:
- "Effect of exchange rate changes" → `fx_effect_on_cash`
- "Cash from discontinued operations" → `cash_from_discontinued_ops`
- "Dividend received from associates" → `dividend_from_associates`

The goal is accurate data capture, not forcing everything into predefined buckets.

## DIVIDEND LINES (consolidated statements of holding companies)

A consolidated cash flow can show TWO separate "dividends paid" lines. They are
DISTINCT economic items and MUST use DISTINCT canonicals:

- "Dividends paid to owners of the holding/parent company" (also "to equity holders
  of the parent", "equity shareholders of the Bank", "owners of the holding
  company", or "equity holders of the parent") → `dividends_paid` — the parent's
  dividend to ITS shareholders; this is what drives dividend yield and payout ratio.
- "Dividends paid to non-controlling interests" / "minority interest" → `dividends_paid_nci`
  — paid by subsidiaries to their outside minority holders; NOT received by the
  parent's shareholders.

Never map a non-controlling-interest line to `dividends_paid`. An unconsolidated
statement has only one "Dividends paid" line → `dividends_paid`.
