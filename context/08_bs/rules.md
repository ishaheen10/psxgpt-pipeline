## EXTRACTION RULES

1. **Section**: Extract ONLY the {section_label} Balance Sheet - ignore other sections
2. **Line items**: Extract each line EXACTLY as shown. Do NOT aggregate. Multiple rows CAN share the same canonical label.
3. **Subtotals**: Bold in both Source Item and Canonical columns
4. **Numbers**: Use comma separators (1,234,567). Convert spaces or other formats.
5. **No hallucination**: Only extract values that exist in the source

## UNIT_TYPE

Look for scale indicators in the document header, footer, or column headers. The key word to find is the SCALE (thousand/million), not the currency.

**Decision rules (check in this order):**
1. If you see "'000", "thousand", "in 000s", or "000's" anywhere → **thousands**
2. If you see "million" or "in millions" → **millions**
3. If you see ONLY "Rupees" or "Rs." with NO scale indicator → **rupees**
4. If no unit indicator found → default to **thousands**

**Common patterns that mean THOUSANDS:**
- "Rupees in thousand" → thousands
- "Rs. in '000" → thousands
- "(Rupees in '000)" → thousands
- "Amount in PKR '000" → thousands
- "(Rs. '000)" → thousands

**IMPORTANT**: "Rupees in thousand" means thousands, NOT rupees. The scale indicator always takes precedence over the currency name.

## CURRENCY

Some pages present data in multiple currencies (e.g., USD and PKR side by side). Always extract the PKR (Pakistan Rupees) values.

## SIGN CONVENTION

Balance sheets use POSITIVE values for all items:
- Assets = POSITIVE: 1,234,567
- Liabilities = POSITIVE: 800,000
- Equity = POSITIVE: 500,000
- Accumulated losses shown in parentheses: (200,000)

Do NOT use parentheses for normal liabilities or equity - only for deficit/loss items.

## REF COLUMN

- **Input rows**: Sequential letters (A, B, C, ..., Z, AA, AB, ...)
- **Calc rows**: Formula showing addition, e.g., F=A+B+C+D+E

## FORMULA VALIDATION

Values from the source are always correct - never adjust numbers to make formulas work.

Formulas must compute correctly:
- If you write F=A+B+C, then F's value must equal A+B+C
- If a formula doesn't validate, adjust the formula (which items are included), not the values

Mirror the source document's subtotal structure - don't impose a standard hierarchy.

## EQUITY FORMULA RULES

- **Authorized share capital** is a disclosure-only memo item showing maximum allowed shares - it is NOT part of actual equity. Only issued/paid-up capital counts.
- **Revaluation surplus** may be presented separately from equity in Pakistani format. Follow the source structure.
- Your formula must produce the exact total shown in the source. If components don't add up, adjust which items are included in the formula.

## DATE COLUMNS

Extract EVERY date column in the table. Do not skip any columns.

**Column header format**: Day + Month + Year (e.g., "31 Dec 2024", "30 Jun 2023")

Balance sheets are point-in-time snapshots, not period ranges. Use the exact date shown in the source.

Common patterns:
- Annual reports: Year-end date (e.g., "31 Dec 2024", "30 Jun 2024")
- Quarterly reports: Quarter-end date (e.g., "30 Sep 2024", "31 Mar 2024")

## BALANCE SHEET STRUCTURE

The balance sheet must balance: **Total Assets = Total Liabilities + Total Equity**

Extract all line items as they appear in the source. Map each to the most appropriate canonical field from the list provided.

## PAGE VALIDATION

If the page does NOT contain a Balance Sheet, output ONLY one of these flags:
- `PAGE_ERROR: NO_BS_FOUND` - Page does not contain any Balance Sheet
- `PAGE_ERROR: PROFIT_LOSS_ONLY` - Page only contains a Profit & Loss statement
- `PAGE_ERROR: CASH_FLOW_ONLY` - Page only contains a Cash Flow statement
- `PAGE_ERROR: NOTES_ONLY` - Page only contains notes to financial statements

If the page contains a Balance Sheet, extract it regardless of any consolidation labels.
