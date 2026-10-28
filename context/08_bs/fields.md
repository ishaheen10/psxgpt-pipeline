Extract the {section_label} Balance Sheet (Statement of Financial Position) from these PSX filing pages.

TICKER: {ticker}
PERIOD: {period}
SECTION: {section_label}
COMPANY TYPE: {company_type}
FISCAL YEAR END: {fy_month_name} ({fiscal_period})
{type_note}

<!-- CANONICAL_SECTIONS -->

## CANONICAL FIELDS

{', '.join(bs_fields)}

**IMPORTANT**: Do NOT force a match to these fields. If a line item doesn't clearly match any canonical field above, create an appropriate snake_case name that accurately describes it. Examples:
- "Right-of-use assets" → `right_of_use_assets`
- "Contract liabilities" → `contract_liabilities`
- "Lease liabilities - current" → `lease_liabilities_current`

The goal is accurate data capture, not forcing everything into predefined buckets.

## PPE SUB-COMPONENTS

When Property, Plant and Equipment (PPE) is presented with sub-components, use distinct canonicals for each:
- `operating_fixed_assets` - Main PPE (operating assets, fixed assets in own use)
- `capital_work_in_progress` - CWIP, construction in progress
- `major_spare_parts` - Spare parts inventory, stand-by equipment
- `development_production_assets` - Oil & gas development/production assets
- `development_decommissioning_costs` - Oil & gas decommissioning costs
- `furniture_equipment` - Furniture and equipment (service companies)
- `right_of_use_assets` - Lease assets under IFRS 16

The **subtotal line** for total PPE should use `property_equipment` with a formula (e.g., W=T+U+V).

Do NOT map all sub-components to `property_equipment` - use the specific canonicals above.
