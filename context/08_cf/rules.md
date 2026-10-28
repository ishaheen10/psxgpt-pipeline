## EXTRACTION RULES

1. **Section**: Extract ONLY the {section_label} Cash Flow - ignore other sections
2. **Line items**: Extract each line EXACTLY as shown. Do NOT aggregate items. Multiple rows MAY share a canonical label ONLY when they are the SAME economic item split across lines. When two lines are economically DISTINCT, assign DISTINCT canonicals — never merge them. In particular, "dividends paid to owners of the parent/holding company" (`dividends_paid`) and "dividends paid to non-controlling interests" (`dividends_paid_nci`) are different items and must never share a canonical; never map a non-controlling-interest line to a parent-level canonical.
3. **Subtotals**: Bold in both Source Item and Canonical columns (cfo, cfi, cff, net_cash_change)
4. **Numbers**: Use comma separators (1,234,567). Convert spaces or other formats.
5. **No hallucination**: Only extract values that exist in the source
6. **Note references are NOT values**: Numbers like "16", "17.1", "20" in a "Note" column are footnote references - ignore them. Only extract values from the numeric period columns.
7. **Column alignment**: Each value must come from the correct period column. Do not shift values between columns.

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

Copy the EXACT signs from the source document. Do NOT flip or interpret signs.
- Numbers in parentheses in source → output with parentheses: (100,000)
- Numbers without parentheses in source → output without: 100,000

## REF COLUMN

- **Input rows**: Sequential letters (A, B, C, ..., Z, AA, AB, ...)
- **Calc rows**: Formula showing addition, e.g., G=A+B+C (formulas use ADDITION ONLY, parentheses handle the sign)

## FORMULA VALIDATION

Values from the source are always correct - never adjust numbers to make formulas work.

Formulas must compute correctly:
- If you write F=A+B+C, then F's value must equal A+B+C
- If a formula doesn't validate, adjust the formula (which items are included), not the values

Mirror the source document's subtotal structure - don't impose a standard hierarchy.

## SUBTOTAL SCOPE

A subtotal's formula includes ONLY the items that belong to that subtotal's section.

Cash flow statements typically start with a base figure (e.g., "Profit before taxation"), followed by adjustment items. The subtotal labeled "Adjustments" or similar sums ONLY the adjustment line items - it does NOT include the starting base figure. The base figure is combined with the adjustments subtotal at the next level (e.g., "Cash from operations before working capital changes").

Match what the source document shows - if a subtotal's displayed value equals only certain rows, those are the rows in its formula.

## PERIOD COLUMNS

Extract EVERY numeric column in the table. Do not skip any columns.

If the source shows BOTH quarterly (3M) AND cumulative (6M/9M) columns for the same period end date, extract ALL of them.

**Column header format**: Duration + Month + Year (e.g., "3M Sep 2024", "12M Jun 2025")

**Duration from source text**:
- "Quarter ended" or "Three months ended" → 3M
- "Half year ended" or "Six months ended" → 6M
- "Nine months ended" → 9M
- "Year ended" or "Annual" → 12M

**This company's fiscal year ends in {fy_month_name} (starts {fy_start_month_name}). For this FY:**
{quarter_mapping}

If source says only "Period ended [date]" without specifying duration, use the mapping above.

## SECTION TOTALS

- **cfo, cfi, cff**: Map ONLY to final section totals, not intermediate subtotals
- Individual line items within each section use their specific canonical fields

## PAGE VALIDATION

If the page does NOT contain a Cash Flow statement, output ONLY one of these flags:
- `PAGE_ERROR: NO_CF_FOUND` - Page does not contain any Cash Flow statement
- `PAGE_ERROR: PROFIT_LOSS_ONLY` - Page only contains a Profit & Loss statement
- `PAGE_ERROR: BALANCE_SHEET_ONLY` - Page only contains a Balance Sheet
- `PAGE_ERROR: NOTES_ONLY` - Page only contains notes to financial statements

If the page contains a Cash Flow statement, extract it regardless of any consolidation labels.
