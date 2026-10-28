# Real first-turn narrative retrieval evaluation

## Executive judgment

Ledger is materially better at ranking than this run's top-line score suggests, but the router is not yet reliable enough for real narrative research.

On 20 anonymized first-turn questions drawn from `psxgpt-serve` production query history:

| Measure | Result |
| --- | ---: |
| Executable retrieval | 12 / 20 |
| Route contract passed | 7 / 20 |
| Success@1, all questions | 7 / 20 |
| Answer-bearing P@3, all questions | 21 / 60 |
| Excellent first-three set, all questions | 3 / 20 |
| Median end-to-end eval latency | 8,286 ms |
| p90 end-to-end eval latency | 16,005 ms |

The most diagnostic split is:

| Route outcome | Success@1 | Answer-bearing P@3 |
| --- | ---: | ---: |
| Route contract passed | 7 / 7 | 18 / 21 |
| Route executed but contract failed | 0 / 5 | 3 / 15 |
| Route did not execute | 0 / 8 | 0 / 24 |

When the controller resolved scope and decomposed the evidence needs correctly, the ranker put an answer-bearing page first in every case. When the route was structurally wrong, rank quality collapsed. The current primary bottleneck is therefore the controller boundary, followed by navigation-page suppression and narrative-page ranking.

## Dataset

The reusable set is [user-narrative-first-turn-v1.json](../../evals/user-narrative-first-turn-v1.json). It contains 20 real questions, with no user, session, message, or trace identifiers.

Freshness was established by taking the first `run_kind='user'` record in each session. The stored history for those records contains only the current user message and an empty assistant placeholder. Questions were selected for narrative posture: business understanding, earnings quality, causal diagnosis, strategy, growth outlook, transaction implications, governance, policy exposure, and cross-company resilience.

Each query has its own answer requirements. Automated route checks use only typed scope and facet expectations. Page relevance was judged manually from the hydrated Markdown, not from family-wide regular expressions.

The run artifact is:

`artifacts/corpus_browser_user_narrative/runs/2026-07-30T15-54-49-621Z/eval_report.json`

Manual labels are in [user-narrative-first-turn-v1-judgments.json](../../evals/user-narrative-first-turn-v1-judgments.json).

## What worked

Three result sets were excellent:

- PRL potential investment: quarterly economics, operating strategy, and a company-specific SWOT appeared as the first three pages.
- Ismail Industries segment outlook: segment earnings, growth strategy, capacity expansion, and segment capital data were immediately available.
- Engro Holdings growth drivers: holding-company earnings, portfolio composition, and segment performance formed a coherent evidence bundle.

Several partial successes were also instructive:

- HINOON surfaced profitability history and the disclosure that the company has one operating segment, but not enough forward evidence about whether growth persists.
- Interloop put a direct explanation of profit pressure at rank one, then fell into a contents page and an irrelevant remuneration note.
- Lucky Core put working-capital history and capital commitments at the top, but the project page stated only a commitment amount rather than the project identities.

These cases show that BM25 plus field-lane fusion can rank narrative evidence well when the controller gives it distinct, source-shaped evidence facets.

## What failed

### 1. Natural company references are outside the executable entity contract

Six questions failed because a real user reference could not be grounded:

- `CYNERGY` and `Cynergico` did not resolve to `CNERGY`.
- `HUBCO` did not resolve to `HUBC`.
- `National Food's` did not resolve to `NATF`.
- `System's` did not resolve to `SYS`.
- AIRLINK is absent from the current entity directory.

This is not a BM25 problem. The model sees the directory, but the validator accepts only a user span that uniquely matches the directory ticker or formal company name. Normal market names, possessives, and corpus coverage therefore stop retrieval before ranking.

The structural fix is an authoritative entity catalog whose records include ticker, formal name, market name, historical names, and observed aliases. The router should select one catalog ID; the validator should validate that ID and the quoted user span, rather than requiring the raw span itself to equal a formal directory string. This is catalog normalization, not a regex fallback.

### 2. Complex narrative plans exceed or conflict with the router boundary

The detailed NATF brief exhausted both eight-second provider attempts. The LOTCHEM investment overview first produced a plan rejected by the industry/corpus invariant and then timed out on retry.

The prompt sends 478 ticker/name/industry rows plus a long planning policy on every call. Complex questions also need the model to emit up to four fully populated facets. Median end-to-end latency was already 8.3 seconds and p90 reached the two-attempt ceiling.

The next router iteration should reduce representational burden without weakening the primary logic:

- represent entities with stable catalog IDs and aliases;
- keep industry empty unless the user requested industry scope;
- make the typed output express evidence needs first and derive repeated route-level unions from those facets;
- measure provider, validation-retry, retrieval, and hydration latency separately;
- evaluate whether the controller needs a longer single attempt or a smaller prompt before retaining two full attempts.

### 3. The controller under-decomposes real narrative questions

Five routes executed but missed required evidence structure:

- Fuel-shock resilience became one filing facet and produced `Shock Absorbers` as rank one; it omitted macro conditions and a separate pass-through/resilience distinction.
- HINOON versus AGP collapsed performance, logistics, imports, regulation, and outlook into one generic facet.
- Interloop budget versus moat omitted macro evidence and searched generic risk pages.
- Cement expansions omitted announcements and collapsed projects plus commissioning timelines into one facet.
- Dynea governance used only a generic compliance facet, omitting ownership, related-party, audit, and board-control evidence.

This confirms that the controller must plan around independently answerable evidence, not merely restate the whole question in one field query. No special-case query rules are warranted; the correction belongs in the general planning contract and its examples.

### 4. Ranking still admits navigation and generic parent pages

Even with a good route, Interloop returned a table of contents at rank two and director remuneration at rank three. Lucky Core returned a page whose body was effectively only “Financial Performance.” Cement results favored historical milestones rather than ongoing projects with go-live dates.

The ranking improvements should remain query-independent:

- penalize high navigation likelihood and very low substantive-content density;
- reward exact evidence relationships in the page Key or direct heading;
- preserve facet coverage in the first visible set;
- distinguish historical milestones from open or forward projects using the page representation, not query-specific rules.

## Recommendation

Do not add more page tags yet and do not add handcrafted query fallbacks.

The next highest-value work is:

1. Replace exact-form entity grounding with a first-class authoritative entity catalog and stable IDs.
2. Simplify the controller output so it only decides information that cannot be derived from its own facets.
3. Strengthen the general evidence-decomposition instruction and test it against this exact holdout.
4. Add query-independent navigation and empty-page quality signals to reranking.
5. Re-run this set and require improvements on the untouched query-specific judgments, especially route execution, Success@1, and answer-bearing P@3.

The evidence supports continuing with a strong router architecture. It does not support adding another LLM reranker or more page tagging at this stage.
