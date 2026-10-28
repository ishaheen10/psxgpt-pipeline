## EXTRACTION RULES

1. **Section**: Extract ONLY the {section_label} P&L - ignore other sections
2. **Line items**: Extract each line EXACTLY as shown. Do NOT aggregate (e.g., keep "Selling expenses" and "Administrative expenses" separate, do not combine into "Operating expenses"). Multiple rows CAN share the same canonical label.
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

- Costs, expenses, taxes = NEGATIVE in parentheses: (800,000)
- Revenue, income, profit = POSITIVE: 1,234,567
- Formulas use ADDITION ONLY: C=A+B (parentheses handle the sign, never use minus)

**"Less:" and "Add:" prefixes**: Line items prefixed with "Less:" are deductions and must be stored as negatives (in parentheses), even if the source shows them without parentheses. Line items prefixed with "Add:" are additions and should be positive.

## REF COLUMN

- **Input rows**: Sequential letters (A, B, C, ..., Z, AA, AB, ...)
- **Calc rows**: Formula showing addition, e.g., C=A+B

## PERIOD COLUMNS

Extract EVERY numeric column in the table. Do not skip any columns.

If the source shows BOTH quarterly (3M) AND cumulative (6M/9M) columns for the same period end date, extract ALL of them. Example: a Sep quarterly filing may have "Quarter ended Sep 30" (3M) AND "Half year ended Sep 30" (6M) - extract both.

**Column header format**: Duration + Month + Year (e.g., "3M Sep 2024", "12M Jun 2025")

**Duration from source text**:
- "Quarter ended" or "Three months ended" → 3M
- "Half year ended" or "Six months ended" → 6M
- "Nine months ended" → 9M
- "Year ended" or "Annual" → 12M

**This company's fiscal year ends in {fy_month_name} (starts {fy_start_month_name}). For this FY:**
{quarter_mapping}

If source says only "Period ended [date]" without specifying duration, use the mapping above.

## PAGE VALIDATION

If pages do NOT contain a {section_label} P&L statement, output ONLY one flag:
- `PAGE_ERROR: NO_PL_FOUND`
- `PAGE_ERROR: BALANCE_SHEET_ONLY`
- `PAGE_ERROR: CASH_FLOW_ONLY`
- `PAGE_ERROR: NOTES_ONLY`

**IMPORTANT**: Extract whatever periods exist on the page, regardless of the filing name. A "quarterly" filing may contain 3M, 6M, or 9M periods - extract ALL of them. The filing name does NOT determine what periods are valid.

**IMPORTANT**: If the page contains a P&L statement (Profit & Loss, Income Statement, Statement of Comprehensive Income), extract it regardless of whether it has a "consolidated" or "unconsolidated" label. Many companies are standalone entities without subsidiaries and won't have consolidation labels - still extract their P&L.
