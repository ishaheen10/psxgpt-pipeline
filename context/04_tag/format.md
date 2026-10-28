## OUTPUT FORMAT

Return a single JSON object with exactly these three keys:

```json
{
  "section_tags": [
    {"tag": "profit_loss", "confidence": 0.95, "basis": "consolidated"},
    {"tag": "balance_sheet", "confidence": 0.88, "basis": "unconsolidated"}
  ],
  "extraction_score": "OK",
  "search_text": "Revenue growth, gross margin 34%, operating leverage, EPS 12.5"
}
```

**section_tags**: array of objects, one per matched tag. Empty array `[]` if no tags apply.
- `tag`: one of the named tags above
- `confidence`: 0.0–1.0 float
- `basis`: `"consolidated"` | `"unconsolidated"` | `"unknown"` for statement tags; `null` for all others

**extraction_score**: exactly one of `"OK"` | `"Fix"` | `"ReOCR"`

**search_text**: string, 18–40 words as described above, or `""` for empty pages
