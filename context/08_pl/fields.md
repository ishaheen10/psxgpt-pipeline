Extract the {section_label} Profit & Loss statement from these PSX filing pages.

TICKER: {ticker}
PERIOD: {period}
SECTION: {section_label}
COMPANY TYPE: {company_type}
FISCAL YEAR END: {fy_month_name} ({fiscal_period})
{type_note}

<!-- CANONICAL_SECTIONS -->

## CANONICAL FIELDS

{', '.join(pl_fields)}

**IMPORTANT**: Do NOT force a match to these fields. If a line item doesn't clearly match any canonical field above, create an appropriate snake_case name that accurately describes it. Examples:
- "Profit from continuing operations" → `net_profit_continuing`
- "Loss from discontinued operations" → `net_profit_discontinued`
- "Share of profit from associates" → `share_of_associates`
- "Workers' Welfare Fund" → `workers_welfare_fund`

The goal is accurate data capture, not forcing everything into predefined buckets.

## UNIQUE CANONICAL FIELDS (ONE OCCURRENCE ONLY)

These key fields must appear EXACTLY ONCE in the output. If you see multiple candidates, use the TOTAL line:

- `gross_profit` - The single gross profit subtotal (revenue minus COGS)
- `operating_profit` - The single operating profit subtotal
- `profit_before_tax` - The single PBT line (before any attribution breakdown)
- `net_profit` - The single TOTAL net profit (before any attribution breakdown). Use `net_profit` even if the company has a loss - negative values represent losses.
- `eps` - The single total EPS figure

**ATTRIBUTION LINES** (appear AFTER net_profit): These show how net_profit is split between owners:
- "Profit attributable to owners of parent" → `net_profit_parent` (NOT net_profit)
- "Profit attributable to non-controlling interests" → `nci_income` (NOT net_profit)
- "Equity holders of the holding company" → `net_profit_parent`

**ASSOCIATES vs NCI**:
- "Share of profit from associates/joint ventures" → `share_of_associates` (NOT other_income, NOT nci_income)
- "Share in profit of associated companies" → `share_of_associates`
- "Non-controlling interests" → `nci_income`

**CONTINUING/DISCONTINUED OPERATIONS**:
- "Profit from continuing operations" / "Profit after tax from core operations" / "Profit after taxation from refinery operations" → `net_profit_continuing` (NOT profit_after_tax_core)
- "Profit/Loss from discontinued operations" → `net_profit_discontinued`
- The TOTAL after both → `net_profit`

**REVENUE FIELDS** (each canonical must appear only once):
- `revenue_net` - Only the FINAL net revenue line after all deductions. If multiple lines build up to net revenue, only the final subtotal gets `revenue_net`.
- `revenue_gross` - Gross revenue/sales before deductions
- `revenue_deductions` - Sales tax, excise duty, rebates, commissions, tariff adjustments, freight margins (these reduce gross to net)

Example: "Gross sales" (A) minus "Sales tax" (B) minus "Rebates" (C) = "Net sales" (D=A+B+C)
→ A: revenue_gross, B: revenue_deductions, C: revenue_deductions, D: revenue_net

**TAXATION FIELDS** (use these canonical names):
- Total taxation line → `taxation` (NOT total_taxation, income_tax, tax_expense)
- Current tax / Current year → `taxation_current` (NOT current_tax, current_tax_year)
- Deferred tax → `taxation_deferred` (NOT deferred_tax, deferred_tax_year)
- Prior period tax → `taxation_prior` (NOT prior_year_tax, current_tax_prior_year)
- If only one taxation line exists, use `taxation`
