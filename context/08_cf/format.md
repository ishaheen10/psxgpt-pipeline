## OUTPUT FORMAT

```markdown
# {ticker} - {period}
UNIT_TYPE: thousands | millions | rupees

## {section_label}

### CASH FLOW
| Source Item | Canonical | Ref | 12M Dec 2024 | 12M Dec 2023 |
|:---|:---|:---|---:|---:|
| [source line item] | [canonical field] | A | 500,000 | 450,000 |
| [source line item] | [canonical field] | B | 50,000 | 45,000 |
| ... | ... | ... | ... | ... |
| **[Operating section total]** | **cfo** | G=A+B+... | 475,000 | 462,000 |
| ... | ... | ... | ... | ... |
| **[Investing section total]** | **cfi** | J=H+I+... | (80,000) | (65,000) |
| ... | ... | ... | ... | ... |
| **[Financing section total]** | **cff** | M=K+L+... | 10,000 | (65,000) |
| [Net change line] | net_cash_change | N=G+J+M | 405,000 | 332,000 |
| [Opening cash line] | cash_start | O | 200,000 | 168,000 |
| [Closing cash line] | cash_end | P=N+O | 605,000 | 200,000 |
```
