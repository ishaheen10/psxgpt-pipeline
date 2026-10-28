# Document boundary audit

Date: 2026-07-31

## Question

How much of the filing corpus can receive deterministic section boundaries from
its table of contents without generating semantic page tags?

## Corpus

- 4,285 full filing PDFs under `database_pdfs/`
- 966 annual reports
- 3,319 quarterly reports
- 265,990 corresponding parsed physical pages available under
  `markdown_pages/`
- 17 full PDFs have no parsed physical pages available for assignment

The 29 macro PDFs are not included in these figures. Most Economic Survey
chapters are already separate source documents, so they have a different
document-boundary problem.

## Method

The audit does not inspect PDF bookmarks or classify section meaning.

1. Use each full PDF path as the canonical document identity.
2. Locate a textual contents page in the existing page Markdown.
3. If the Markdown missed it, extract only the opening 15 PDF pages with
   `pdftotext` and locate the contents page there.
4. Parse ordered `(section title, printed page)` entries.
5. Match several TOC titles against headings or opening text on physical
   Markdown pages.
6. Infer one printed-to-physical page offset for the document.
7. Accept complete ranges only when at least five ordered anchors agree within
   two pages, at least 70% of ordered anchors support the offset, and the
   anchors span at least 20% of the document.

There are no issuer-specific section names, filing-specific aliases, or
semantic fallback tags in the audit.

## Results

| Confidence | Documents | Share | Sections available | Parsed pages in documents |
| --- | ---: | ---: | ---: | ---: |
| Strong complete ranges | 2,096 | 48.9% | 35,732 | 151,888 |
| Verified partial ranges | 550 | 12.8% | 4,423 | 42,538 |
| Not boundary-ready | 1,639 | 38.2% | — | 71,564 |
| Total | 4,285 | 100.0% | — | 265,990 |

Complete plus partial coverage reaches 2,646 documents (61.8%) and 194,426
parsed pages (73.1%).

### By filing type

| Filing type | Documents | Strong | Strong or partial |
| --- | ---: | ---: | ---: |
| Annual | 966 | 530 (54.9%) | 716 (74.1%) |
| Quarterly | 3,319 | 1,566 (47.2%) | 1,930 (58.2%) |

For documents longer than 20 pages, 1,733 of 3,012 (57.5%) have strong complete
ranges and 2,195 (72.9%) have strong or partial ranges.

## Funnel and residuals

- A textual TOC was located in 3,196 documents.
- At least three usable TOC entries were parsed in 2,900 documents.
- 2,096 then supported a stable document-level physical-page offset.
- 550 supported only verified partial ranges.

The 1,639 not-ready documents break down as:

- 1,089: no textual TOC located;
- 296: TOC located but fewer than three usable entries parsed;
- 254: at least three entries parsed but insufficient ordered destination
  alignment;
- 17 of the above have no parsed physical pages available at all.

Short filings explain much of the residual: 822 of the not-ready documents have
20 or fewer parsed pages, and 1,195 have 40 or fewer.

## Interpretation

The immediately safe production target is the 2,096 strong documents. Their
TOC entries can be converted into complete physical-page ranges without
semantic tagging.

The 550 partial documents should retain only their verified anchor-to-anchor
ranges. They should not receive inferred whole-document ranges yet.

Each materialized boundary should retain:

- `document_id`
- `section_title`
- `toc_physical_page`
- `printed_start_page`
- `physical_start_page`
- `physical_end_page`
- `inferred_page_offset`
- `alignment_anchor_count`
- `alignment_confidence`

Pages can inherit `section_title` as searchable document context. This is a
source-derived breadcrumb, not a predicted page label.

## Reproduction

```bash
.venv/bin/python apps/corpus-browser/scripts/audit-document-boundaries.py --workers 8
```

Machine-readable artifacts:

- `artifacts/corpus_browser_document_boundaries/audit_report.json`
- `artifacts/corpus_browser_document_boundaries/document_audit.jsonl`

