# Page Index V2 — 1,000-page pilot findings

Run date: 2026-07-29
Model: `deepseek-v4-flash`
Prompt: `2026-07-29.1`
Sample seed: `20260729`

## Decision

DeepSeek V4 Flash is good enough to generate candidate retrieval keys in bulk,
but its output must remain behind deterministic grounding and quality gates.
The signals improve top-rank retrieval most clearly for macro pages that do not
already have `search_text`. They are not yet strong enough to replace raw-text
retrieval or the current filing keys.

The safest next use is an additive key lane for macro and authoritative pages,
followed by an independent eval using real user queries and page-level qrels.

## Sample

| Cohort | Pages |
| --- | ---: |
| Filing statements | 160 |
| Other filing pages | 340 |
| Economic Survey | 150 |
| Other macro | 100 |
| Announcements | 150 |
| Other authoritative pages | 100 |
| **Total** | **1,000** |

## Generation quality

| Signal | Result |
| --- | ---: |
| Successful API outputs | 1,000 / 1,000 |
| Parse status `ok` | 937 |
| Parse status `incomplete` | 54 |
| Parse status `reextract` | 9 |
| Pages with `search_text` | 992 |
| Pages with evidence blocks | 980 |
| Evidence blocks | 2,376 |
| Verbatim anchors grounded in Markdown | 6,823 / 7,064 (96.59%) |
| Pages eligible for V2 indexing after gates | 990 |
| Input-truncated pages | 25 |
| Cost | $0.724933 |
| Median / p90 generation latency | 12.1s / 29.2s |

One page containing only `.` was incorrectly called `ok`. Nine `reextract`
pages and that contract contradiction are gated out of the V2 signal lane. Raw
Markdown responses are retained for audit, and only grounded anchors enter the
shadow index.

The manual cross-cohort inspection found the evidence descriptions generally
specific and useful. It also surfaced an upstream corpus problem: a file under
the budget corpus contains an unrelated 2023 corporate financial summary. The
model described that content faithfully but did not flag its conflict with the
trusted document metadata. A future schema should record metadata/content
alignment as a retrieval gate rather than trying to repair it in ranking.

## Shadow BM25 result

The evaluation searches the same 1,000 pages. Queries are derived from the
existing filing search key or a heading read directly from source Markdown;
they are not generated from V2 descriptions.

| Representation | MRR@20 | Recall@1 | Recall@5 | Recall@20 |
| --- | ---: | ---: | ---: | ---: |
| Raw Markdown | 0.8206 | 77.87% | 86.66% | 95.55% |
| Current fields | 0.8243 | 78.39% | 86.97% | 95.76% |
| Current + V2 fields | **0.8382** | **80.35%** | **87.69%** | 95.14% |
| Current/V2-key rank fusion | 0.8287 | 78.70% | 87.49% | 95.35% |

V2 improves early precision but slightly reduces tail recall. This means the
new fields should be treated as Keys, while original Markdown remains the
Value and its own retrieval lane.

The strongest cohort gains in the combined index were:

| Cohort | Current MRR@20 | V2 MRR@20 | Change |
| --- | ---: | ---: | ---: |
| Other macro | 0.4584 | 0.5205 | +13.5% |
| Economic Survey | 0.4389 | 0.4869 | +10.9% |
| Other authoritative | 0.5591 | 0.5709 | +2.1% |

Filings did not move because existing filing `search_text` already put the
target page first in this rediscovery test. Announcements also stayed flat on
title-based rediscovery; user-shaped announcement queries still require the
router to select the announcement corpus and entity before page ranking.

## What this does and does not prove

It proves that an open-world LLM page key can materially improve ranking where
no compact key currently exists, especially macro material. It does not prove
that answer-ready multi-page context improves, because the pilot has no
independent qrels for actual user queries. It also does not validate queries
whose relevant page was absent from the 1,000-page sample, such as the sampled
version of the UBL insider-transactions query.

Before a full-corpus pass:

1. Add a generalized metadata/content-alignment judgment and gate conflicts.
2. Avoid truncating long pages, or explicitly mark the unprocessed region.
3. Shorten output: 1.76 million output tokens for 1,000 pages is more verbose
   than retrieval needs.
4. Build page-level qrels from real D1 query shapes, then measure Recall@5,
   Recall@10, first-page quality, and smallest answer-ready context.
5. Roll out first to macro and authoritative collections, where this pilot
   measured the clearest gain; retain existing filing keys.
