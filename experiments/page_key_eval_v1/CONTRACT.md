# Page-Key Retrieval Evaluation V1

## Status

Predeclared replacement for the invalidated Page Index V3 comparison.

No ranking result is valid unless every gate in this contract passes.

## Question

Does a grounded compact page Key add useful candidate recall and improve the
first visible pages when added as one independent retrieval lane to the
unchanged production search architecture?

## Separate Boundaries

The evaluation has three separately reported stages:

1. **Router:** natural query -> entities, industries, corpora, periods,
   cadence, evidence facets, and four field queries including `key`.
2. **Candidate retrieval:** frozen route -> production-identical existing
   lanes, plus an independently scored Key-only lane.
3. **Visible ranking:** unchanged production ranker versus the same ranker
   with one predeclared Key-lane contribution.

A later-stage success cannot hide an earlier-stage failure.

## Router Contract

The router knows these searchable representations:

- `key`: grounded evidence relationships, measurements, actions, breakdowns,
  comparison periods, and likely analyst search language;
- `summary`: the existing compact indexed summary;
- `text`: original Markdown;
- `heading`: filing heading, announcement title, or macro chapter.

Every query is at most four words. The Key query must not contain ticker,
period, corpus, or generic document words handled by filters.

The router knows these collections:

- `filing`: annual/quarterly statements, notes, operating tables, accounting
  policies, governance, and management narrative;
- `announcement`: dated PSX notices and event attachments;
- `macro`: Economic Survey, SBP, budget, and other economy-wide publications.

Router scoring uses frozen human expectations for entity, corpus, temporal
mode, cadence, industry, and evidence-facet separation. JSON validity alone is
not a pass.

## Query Set

- Queries are real production shapes or explicit challenge queries derived
  from observed failures.
- Each distinct natural query reaches the router.
- Paraphrases that collapse to one frozen route are not counted as independent
  ranking tests.
- The full set spans filing tables, filing narrative, accounting policy,
  governance, announcements, macro publications, industry scope, explicit and
  inferred source scope, latest/bounded/all periods, multi-entity comparison,
  multi-facet needs, and conversational continuation.
- The first smoke run uses only cases marked `smoke=true`.

## Corpus Selection

- No ranker may select evaluation pages.
- Filing evaluation uses complete, deterministically chosen source documents,
  not query-selected pages.
- Announcement and macro samples are deterministic strata selected before
  either ranker runs.
- Every page in the small evaluation corpus receives the same versioned Key
  generation treatment.
- Corpus membership and exclusions are frozen before retrieval.

## Ranker Parity Gate

Before adding the Key lane, the local evaluator must reproduce the deployed
baseline's top 20 candidates for the frozen routes within a predeclared parity
tolerance. It must use the same:

- FTS fields and tokenizer;
- corpus-specific queries;
- filters and latest-period rules;
- candidate limits;
- lane weights and RRF;
- entity balancing;
- navigation suppression;
- structural reranking.

If parity fails, Key evaluation stops.

## Clean Ablation

The baseline tables, fields, weights, IDF, filters, and ranker remain
unchanged.

The generated Key lives in a separate FTS table. Report:

1. Key-only Recall@20;
2. baseline Recall@20 and visible metrics;
3. baseline plus the frozen Key-lane rule.

No weight is tuned on the test set. Any tuning uses a separate development
split.

## Relevance Evidence

- Qrels are frozen before final scoring.
- A relevance judge sees original Markdown evidence, never generated keys,
  lane names, or ranks.
- The excerpt contract must cover the matched source evidence; a head-only
  truncation is forbidden.
- A human reviews every router failure, every changed top result, and the
  largest positive and negative ranking movements.

## Primary Metrics

- router scope accuracy;
- router facet accuracy;
- candidate Recall@20;
- first-result answer-bearing rate;
- answer-bearing Precision@5;
- requested-entity coverage@5;
- corpus, period, and cadence correctness;
- smallest sufficient evidence-set success;
- median and p90 latency.

## Integrity

- Query set, corpus manifest, route outputs, candidate sets, qrels, prompt
  version, model, and costs are immutable versioned artifacts.
- Cache keys include prompt hash, query/corpus hash, and candidate-set hash.
- Spend is append-only.
- Exclusions are declared before a run.
- Reports explicitly distinguish smoke, development, and held-out test data.
