## OUTPUT FORMAT

```markdown
# {ticker} - {period}
UNIT_TYPE: thousands | millions | rupees

## {section_label}

### BALANCE SHEET
| Source Item | Canonical | Ref | 31 Dec 2024 | 31 Dec 2023 |
|:---|:---|:---|---:|---:|
| [Source line item] | [canonical_field] | A | 500,000 | 450,000 |
| [Source line item] | [canonical_field] | B | 200,000 | 180,000 |
| [Source line item] | [canonical_field] | C | 800,000 | 750,000 |
| **[Subtotal line]** | **[canonical_field]** | D=A+B+C | 1,500,000 | 1,250,000 |
...
| **Total assets** | **total_assets** | ... | ... | ... |
...
| **Total liabilities** | **total_liabilities** | ... | ... | ... |
...
| **Total equity** | **total_equity** | ... | ... | ... |
| **Total equity and liabilities** | **total_equity_and_liabilities** | ... | ... | ... |
```
