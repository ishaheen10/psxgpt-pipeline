## OUTPUT FORMAT

```markdown
# {ticker} - {period}
UNIT_TYPE: thousands | millions | rupees

## {section_label}

### PROFIT & LOSS
| Source Item | Canonical | Ref | 12M Dec 2024 | 12M Dec 2023 |
|:---|:---|:---|---:|---:|
| Revenue | revenue_net | A | 1,234,567 | 1,100,000 |
| Cost of sales | cost_of_revenue | B | (800,000) | (700,000) |
| **Gross profit** | **gross_profit** | C=A+B | 434,567 | 400,000 |
| Selling expenses | operating_expenses | D | (80,000) | (60,000) |
| Administrative expenses | operating_expenses | E | (50,000) | (40,000) |
| Other charges | operating_expenses | F | (20,000) | (20,000) |
| Other income | other_income | G | 10,000 | 8,000 |
| **Operating profit** | **operating_profit** | H=C+D+E+F+G | 294,567 | 288,000 |
...
```
