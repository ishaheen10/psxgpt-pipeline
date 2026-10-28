#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  materializeModelRoute,
  routeWithModel,
} from "../app/api/route/route.ts";
import {
  executeRetrievalPlan,
  selectContextBundle,
} from "../app/lib/retrieval-execution.ts";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = resolve(appRoot, "../..");
const outputRoot = resolve(projectRoot, "artifacts/corpus_browser_key_ablation");
const runId = new Date().toISOString().replaceAll(":", "-");
const runRoot = join(outputRoot, "runs", runId);
const caseRoot = join(runRoot, "cases");
const judgeCacheRoot = join(outputRoot, "judge-cache");
const queryPath = resolve(appRoot, "evals/key-ablation-50.json");
const reportPath = join(outputRoot, "eval_report.json");
const summaryPath = join(outputRoot, "eval_report.md");
const keyRowsPath = join(outputRoot, "page_keys.jsonl");
const baseUrl = (
  process.argv.find((value) => value.startsWith("--base-url="))?.slice("--base-url=".length)
  || "https://psxgpt-corpus-api-key-eval.isfandiyar-shaheen.workers.dev"
).replace(/\/$/, "");
const limitCases = Number(
  process.argv.find((value) => value.startsWith("--limit="))?.slice("--limit=".length)
  || 50,
);
const requestedCaseIds = new Set(
  (process.argv.find((value) => value.startsWith("--case="))?.slice("--case=".length) || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);
const refresh = process.argv.includes("--refresh");
const modes = ["baseline", "key_only", "combined"];
const referenceDate = "2026-07-29";
const evaluationVersion = "ledger-key-ablation.v3";
const pageJudgePromptVersion = "ledger-key-page-judge.v3";
const routeJudgePromptVersion = "ledger-route-judge.v1";

try {
  process.loadEnvFile(resolve(projectRoot, ".env"));
} catch {
  // The evaluator will fail explicitly below if the model credential is absent.
}

function stableOrder(value) {
  return createHash("sha256").update(value).digest("hex");
}

function hashJson(value) {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function percentile(values, fraction) {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))];
}

function dcg(relevances) {
  return relevances.reduce(
    (sum, relevance, index) => sum + ((2 ** relevance) - 1) / Math.log2(index + 2),
    0,
  );
}

function metricForResults(results, judgments) {
  const top = results.slice(0, 5);
  const relevances = top.map((result) =>
    judgments.get(`${result.corpus}:${result.id}`)?.relevance || 0
  );
  const ideal = [...judgments.values()]
    .map((judgment) => judgment.relevance)
    .sort((left, right) => right - left)
    .slice(0, 5);
  const idealDcg = dcg(ideal);
  const firstRelevant = relevances.findIndex((value) => value >= 2);
  return {
    ndcg_at_5: idealDcg ? dcg(relevances) / idealDcg : 0,
    precision_at_5: top.length
      ? relevances.filter((value) => value >= 2).length / top.length
      : 0,
    reciprocal_rank: firstRelevant >= 0 ? 1 / (firstRelevant + 1) : 0,
    direct_at_1: relevances[0] === 3,
    useful_at_3: relevances.slice(0, 3).filter((value) => value >= 2).length,
    excellent_context_at_3:
      relevances.slice(0, 3).length === 3
      && relevances.slice(0, 3).every((value) => value >= 2)
      && relevances.slice(0, 3).some((value) => value === 3),
    top_relevances: relevances,
  };
}

function contextMetric(bundle, judgments, plan) {
  const scored = bundle.map((result) => ({
    result,
    judgment: judgments.get(`${result.corpus}:${result.id}`),
  }));
  const useful = scored.filter(({ judgment }) => (judgment?.relevance || 0) >= 2);
  const requestedEntities = [...new Set(plan.tickers || [])];
  const representedEntities = new Set(bundle.map((result) => result.entity));
  const usefulEntities = new Set(useful.map(({ result }) => result.entity));
  const facetLabels = (plan.searchFacets || []).map((facet) => facet.label);
  const requiredCells = facetLabels.flatMap((facet) =>
    requestedEntities.map((entity) => ({ facet, entity }))
  );
  const coveredCells = requiredCells.filter(({ facet, entity }) =>
    bundle.some((result) =>
      result.entity === entity && (result.retrieval_facets || []).includes(facet)
    )
  );
  const requestedEntityCoverage = requestedEntities.length
    ? requestedEntities.filter((entity) => representedEntities.has(entity)).length / requestedEntities.length
    : 1;
  const usefulEntityCoverage = requestedEntities.length
    ? requestedEntities.filter((entity) => usefulEntities.has(entity)).length / requestedEntities.length
    : 1;
  const facetEntityCoverage = requiredCells.length
    ? coveredCells.length / requiredCells.length
    : 1;
  const usefulPrecision = bundle.length ? useful.length / bundle.length : 0;
  const hasDirectEvidence = scored.some(({ judgment }) => judgment?.relevance === 3);
  return {
    bundle_size: bundle.length,
    useful_precision: usefulPrecision,
    requested_entity_coverage: requestedEntityCoverage,
    useful_entity_coverage: usefulEntityCoverage,
    facet_entity_coverage: facetEntityCoverage,
    has_direct_evidence: hasDirectEvidence,
    excellent_context:
      bundle.length > 0
      && requestedEntityCoverage === 1
      && usefulEntityCoverage === 1
      && facetEntityCoverage === 1
      && usefulPrecision >= 0.5
      && hasDirectEvidence,
  };
}

function aggregate(cases, mode, qualifiedOnly, include = () => true) {
  const rows = cases.filter((item) =>
    item.modes?.[mode]?.results
    && include(item)
    && (!qualifiedOnly || item.judge?.route?.score >= 2)
  );
  const scored = rows.map((item) =>
    metricForResults(
      item.modes[mode].results,
      new Map(item.judge.judgments.map((judgment) => [judgment.candidate_key, judgment])),
    )
  );
  const contexts = rows.map((item) =>
    contextMetric(
      item.modes[mode].context_bundle || [],
      new Map(item.judge.judgments.map((judgment) => [judgment.candidate_key, judgment])),
      item.route,
    )
  );
  const latency = rows.map((item) => item.modes[mode].latency_ms);
  const keyVisible = rows.flatMap((item) => item.modes[mode].results.slice(0, 5));
  return {
    cases: rows.length,
    mean_ndcg_at_5: scored.length
      ? scored.reduce((sum, value) => sum + value.ndcg_at_5, 0) / scored.length
      : 0,
    mean_precision_at_5: scored.length
      ? scored.reduce((sum, value) => sum + value.precision_at_5, 0) / scored.length
      : 0,
    mean_reciprocal_rank: scored.length
      ? scored.reduce((sum, value) => sum + value.reciprocal_rank, 0) / scored.length
      : 0,
    direct_page_at_1_rate: scored.length
      ? scored.filter((value) => value.direct_at_1).length / scored.length
      : 0,
    excellent_context_at_3_rate: scored.length
      ? scored.filter((value) => value.excellent_context_at_3).length / scored.length
      : 0,
    compiled_context_excellent_rate: contexts.length
      ? contexts.filter((value) => value.excellent_context).length / contexts.length
      : 0,
    mean_compiled_context_precision: contexts.length
      ? contexts.reduce((sum, value) => sum + value.useful_precision, 0) / contexts.length
      : 0,
    mean_requested_entity_coverage: contexts.length
      ? contexts.reduce((sum, value) => sum + value.requested_entity_coverage, 0) / contexts.length
      : 0,
    mean_useful_entity_coverage: contexts.length
      ? contexts.reduce((sum, value) => sum + value.useful_entity_coverage, 0) / contexts.length
      : 0,
    mean_facet_entity_coverage: contexts.length
      ? contexts.reduce((sum, value) => sum + value.facet_entity_coverage, 0) / contexts.length
      : 0,
    median_latency_ms: percentile(latency, 0.5),
    p90_latency_ms: percentile(latency, 0.9),
    generated_summary_coverage_at_5: keyVisible.length
      ? keyVisible.filter((result) => result.page_index?.display_summary).length / keyVisible.length
      : 0,
  };
}

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: { accept: "application/json" },
    signal: AbortSignal.timeout(20_000),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${text.slice(0, 300)}`);
  return JSON.parse(text);
}

async function documentText(candidate) {
  const url = new URL("/api/document", baseUrl);
  url.searchParams.set("corpus", candidate.corpus);
  url.searchParams.set("id", String(candidate.id));
  try {
    const detail = await fetchJson(url);
    return String(detail.text || "").slice(0, 18_000);
  } catch (error) {
    return `[Document load failed: ${error instanceof Error ? error.message : String(error)}]`;
  }
}

async function buildJudgeEvidence(testCase, pool) {
  const evidence = [];
  for (const candidate of [...pool].sort((left, right) =>
    stableOrder(`${testCase.id}:${left.corpus}:${left.id}`)
      .localeCompare(stableOrder(`${testCase.id}:${right.corpus}:${right.id}`))
  )) {
    evidence.push({
      candidate_key: `${candidate.corpus}:${candidate.id}`,
      corpus: candidate.corpus,
      entity: candidate.entity,
      period: candidate.period,
      title: candidate.title,
      generated_display_summary: candidate.page_index?.display_summary || "",
      current_search_summary: candidate.summary,
      matched_passage: candidate.snippet,
      page_text: await documentText(candidate),
    });
  }
  return evidence;
}

async function judgeCase(testCase, plan, evidence) {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) throw new Error("GEMINI_API_KEY is required for page-level evaluation.");
  const schema = {
    type: "json_schema",
    json_schema: {
      name: "retrieval_ablation_judgment",
      strict: true,
      schema: {
        type: "object",
        additionalProperties: false,
        properties: {
          pooled_evidence_available: { type: "boolean" },
          judgments: {
            type: "array",
            items: {
              type: "object",
              additionalProperties: false,
              properties: {
                candidate_key: { type: "string" },
                relevance: { type: "integer", minimum: 0, maximum: 3 },
                evidence_quality: { type: "integer", minimum: 0, maximum: 2 },
                entity_correct: { type: "boolean" },
                period_correct: { type: "boolean" },
                reason: { type: "string" },
              },
              required: [
                "candidate_key", "relevance", "evidence_quality",
                "entity_correct", "period_correct", "reason",
              ],
            },
          },
        },
        required: ["pooled_evidence_available", "judgments"],
      },
    },
  };
  const system = [
    "You are evaluating page retrieval for an authoritative Pakistani document browser.",
    "Use only the provided page evidence. Do not infer that a page contains evidence not shown.",
    "pooled_evidence_available is true when at least one supplied candidate is useful supporting evidence or direct evidence.",
    "Candidate relevance: 3 directly answers a material evidence need; 2 useful supporting evidence; 1 topically related but not useful; 0 irrelevant or wrong entity/period/corpus.",
    "Evidence quality: 2 page contains specific facts/table/narrative suitable for analyst context; 1 useful but generic/partial; 0 no usable evidence.",
    "For multi-part queries a page can score 3 by directly answering one material evidence need.",
    "Do not reward a candidate for merely repeating query words, a cover page, a table of contents, or metadata.",
    "Return one judgment for every candidate_key exactly once.",
  ].join(" ");
  const response = await fetch("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: "gemini-3.5-flash-lite",
      reasoning_effort: "low",
      response_format: schema,
      messages: [
        { role: "system", content: system },
        {
          role: "user",
          content: JSON.stringify({
            query: testCase.query,
            requested_entities: plan.tickers,
            requested_period: {
              years: plan.years,
              date_from: plan.dateFrom,
              date_to: plan.dateTo,
              latest: plan.latest,
            },
            evidence_needs: plan.evidenceNeeds,
            candidates: evidence,
          }),
        },
      ],
    }),
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) throw new Error(`Judge HTTP ${response.status}: ${(await response.text()).slice(0, 500)}`);
  const payload = await response.json();
  const content = payload.choices?.[0]?.message?.content;
  if (!content) throw new Error("Judge returned no content.");
  const judgment = JSON.parse(content);
  const expected = new Set(evidence.map((item) => item.candidate_key));
  const returned = new Set(judgment.judgments.map((item) => item.candidate_key));
  if (expected.size !== returned.size || [...expected].some((key) => !returned.has(key))) {
    throw new Error("Judge did not return the complete pooled candidate set.");
  }
  return {
    judgment,
    usage: payload.usage || null,
    model: payload.model || "gemini-3.5-flash-lite",
  };
}

async function judgeRoute(testCase, plan) {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) throw new Error("GEMINI_API_KEY is required for route evaluation.");
  const schema = {
    type: "json_schema",
    json_schema: {
      name: "retrieval_route_judgment",
      strict: true,
      schema: {
        type: "object",
        additionalProperties: false,
        properties: {
          score: { type: "integer", minimum: 0, maximum: 3 },
          error_type: {
            type: "string",
            enum: ["none", "wrong_corpus", "wrong_entity", "wrong_time", "wrong_decomposition", "multiple"],
          },
          notes: { type: "string" },
        },
        required: ["score", "error_type", "notes"],
      },
    },
  };
  const system = [
    "You are evaluating only a retrieval controller plan for an authoritative Pakistani document browser.",
    "You cannot see retrieved pages. Do not speculate about what retrieval returned or whether a particular fact exists in the corpus.",
    "Judge whether the plan faithfully translates the request into corpus, entity, temporal, cadence, evidence-need, facet, and field-query scope.",
    "A cross-company comparison can be correctly decomposed by searching each requested company for the same evidence; one page need not compare the companies itself.",
    "Score 3 for correct scope and decomposition, 2 for a minor omission that remains usable, 1 for a material defect, and 0 for an unusable plan.",
    "Use error_type none only for scores 2 or 3.",
  ].join(" ");
  const response = await fetch("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: "gemini-3.5-flash-lite",
      reasoning_effort: "low",
      response_format: schema,
      messages: [
        { role: "system", content: system },
        {
          role: "user",
          content: JSON.stringify({
            query: testCase.query,
            retrieval_plan: plan,
          }),
        },
      ],
    }),
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) throw new Error(`Route judge HTTP ${response.status}: ${(await response.text()).slice(0, 500)}`);
  const payload = await response.json();
  const content = payload.choices?.[0]?.message?.content;
  if (!content) throw new Error("Route judge returned no content.");
  return {
    judgment: JSON.parse(content),
    usage: payload.usage || null,
    model: payload.model || "gemini-3.5-flash-lite",
  };
}

async function readOrCreateJudgment(promptVersion, input, create) {
  const inputHash = hashJson({ prompt_version: promptVersion, ...input });
  const cachePath = join(judgeCacheRoot, `${inputHash}.json`);
  let record = !refresh
    ? await readFile(cachePath, "utf8").then(JSON.parse).catch(() => null)
    : null;
  if (!record) {
    const judged = await create();
    record = {
      prompt_version: promptVersion,
      input_hash: inputHash,
      created_at: new Date().toISOString(),
      model: judged.model,
      usage: judged.usage,
      judgment: judged.judgment,
    };
    await writeFile(cachePath, `${JSON.stringify(record, null, 2)}\n`, { flag: "wx" })
      .catch((error) => {
        if (error?.code !== "EEXIST") throw error;
      });
  }
  return { inputHash, record };
}

async function evaluateCase(testCase, entities, keyIndex) {
  const casePath = join(caseRoot, `${testCase.id}.json`);
  const started = performance.now();
  const modelRoute = await routeWithModel(
    testCase.query,
    entities,
    referenceDate,
    null,
    { reasoningEffort: "low", timeoutMs: 8_000 },
  );
  const plan = materializeModelRoute(testCase.query, modelRoute, entities, null);
  const modeResults = {};
  for (const mode of modes) {
    const modeStarted = performance.now();
    try {
      const response = await executeRetrievalPlan(plan, {
        fetcher: fetch,
        searchPath: `${baseUrl}/api/search`,
        retrievalMode: mode,
        debug: true,
      });
      modeResults[mode] = {
        latency_ms: Math.round(performance.now() - modeStarted),
        results: response.results,
        context_bundle: selectContextBundle(response, plan),
        diagnostics: response.diagnostics,
      };
    } catch (error) {
      modeResults[mode] = {
        latency_ms: Math.round(performance.now() - modeStarted),
        error: error instanceof Error ? error.message : String(error),
        results: [],
      };
    }
  }
  const poolMap = new Map();
  for (const mode of modes) {
    for (const result of [
      ...modeResults[mode].results.slice(0, 5),
      ...(modeResults[mode].context_bundle || []),
    ]) {
      poolMap.set(`${result.corpus}:${result.id}`, result);
    }
  }
  const evidence = await buildJudgeEvidence(testCase, [...poolMap.values()]);
  const [pageJudge, routeJudge] = await Promise.all([
    readOrCreateJudgment(
      pageJudgePromptVersion,
      { query: testCase.query, retrieval_plan: plan, candidates: evidence },
      () => judgeCase(testCase, plan, evidence),
    ),
    readOrCreateJudgment(
      routeJudgePromptVersion,
      { query: testCase.query, retrieval_plan: plan },
      () => judgeRoute(testCase, plan),
    ),
  ]);
  const baselineTop = modeResults.baseline.results.slice(0, 5);
  const baselineKeyCoverage = baselineTop.length
    ? baselineTop.filter((result) => keyIndex.has(`${result.corpus}:${result.id}`)).length
      / baselineTop.length
    : 0;
  const evaluated = {
    id: testCase.id,
    source: testCase.source,
    query: testCase.query,
    evaluation_version: evaluationVersion,
    retrieval_version: "ledger-retrieval.v3",
    route: plan,
    modes: modeResults,
    sidecar_coverage: {
      baseline_top_5: baselineKeyCoverage,
      scope: plan.corpora.every((corpus) => corpus === "filing")
        ? "selective_filing_sample"
        : plan.corpora.some((corpus) => corpus === "filing")
          ? "mixed"
          : "complete_source_sample_with_quality_gate",
    },
    judge_cache: {
      page: {
        prompt_version: pageJudgePromptVersion,
        input_hash: pageJudge.inputHash,
        usage: pageJudge.record.usage,
        model: pageJudge.record.model,
      },
      route: {
        prompt_version: routeJudgePromptVersion,
        input_hash: routeJudge.inputHash,
        usage: routeJudge.record.usage,
        model: routeJudge.record.model,
      },
    },
    judge: {
      pooled_evidence_available: pageJudge.record.judgment.pooled_evidence_available,
      route: routeJudge.record.judgment,
      judgments: pageJudge.record.judgment.judgments,
    },
    total_latency_ms: Math.round(performance.now() - started),
  };
  await writeFile(casePath, `${JSON.stringify(evaluated, null, 2)}\n`);
  return evaluated;
}

function markdownSummary(report) {
  const rows = modes.map((mode) => {
    const value = report.ranker_qualified[mode];
    return `| ${mode} | ${value.cases} | ${value.mean_ndcg_at_5.toFixed(3)} | ${value.mean_precision_at_5.toFixed(3)} | ${(value.direct_page_at_1_rate * 100).toFixed(1)}% | ${(value.compiled_context_excellent_rate * 100).toFixed(1)}% | ${(value.mean_useful_entity_coverage * 100).toFixed(1)}% | ${value.median_latency_ms} | ${value.p90_latency_ms} |`;
  });
  return [
    "# Corpus Browser Key Ablation",
    "",
    `Generated: ${report.generated_at}`,
    "",
    `Retrieval version: \`${report.retrieval_version}\``,
    "",
    `Evaluation version: \`${report.evaluation_version}\`; immutable run: \`${report.run_id}\`.`,
    "",
    `Queries: ${report.total_cases}; pooled evidence available: ${report.pooled_evidence_cases}; ranker-qualified: ${report.ranker_qualified_cases}; router failures: ${report.router_failures.length}.`,
    "",
    "| Mode | Cases | nDCG@5 | P@5 | Direct page @1 | Excellent compiled context | Useful entity coverage | Median ms | P90 ms |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...rows,
    "",
    "## Coverage boundary",
    "",
    `The Key sidecars cover a query-selected filing sample, not the full filing corpus. Combined-versus-baseline is a valid partial-rollout measurement; Key-only filing results are not a fair estimate of a complete Key index. ${report.coverage_segments.high_candidate_coverage_cases} ranker-qualified cases had at least 80% Key coverage among the baseline top five; ${report.coverage_segments.low_candidate_coverage_cases} did not.`,
    "",
    "## Pairwise wins",
    "",
    ...Object.entries(report.pairwise_ndcg_wins).map(([pair, value]) =>
      `- ${pair}: ${value.left} / ${value.ties} / ${value.right}`
    ),
    "",
    "## Router failures",
    "",
    ...report.router_failures.map((item) =>
      `- ${item.id}: ${item.error_type} — ${item.notes}`
    ),
    "",
    "## Largest combined-mode regressions",
    "",
    ...report.largest_combined_regressions.map((item) =>
      `- ${item.id}: baseline ${item.baseline.toFixed(3)}, combined ${item.combined.toFixed(3)} — ${item.query}`
    ),
    "",
  ].join("\n");
}

async function loadKeyIndex() {
  const rows = (await readFile(keyRowsPath, "utf8")).split("\n").filter(Boolean);
  return new Set(rows.map((line) => {
    const row = JSON.parse(line);
    return `${row.corpus}:${row.page_id}`;
  }));
}

async function main() {
  await Promise.all([
    mkdir(caseRoot, { recursive: true }),
    mkdir(judgeCacheRoot, { recursive: true }),
  ]);
  const allQueries = JSON.parse(await readFile(queryPath, "utf8"));
  const queries = (
    requestedCaseIds.size
      ? allQueries.filter((item) => requestedCaseIds.has(item.id))
      : allQueries
  ).slice(0, limitCases);
  const keyIndex = await loadKeyIndex();
  const directory = await fetchJson(`${baseUrl}/api/entities`);
  const entities = (directory.entities || []).filter((item) => item.ticker && item.company_name);
  const evaluated = [];
  for (const [index, testCase] of queries.entries()) {
    try {
      evaluated.push(await evaluateCase(testCase, entities, keyIndex));
      process.stderr.write(`[${index + 1}/${queries.length}] ${testCase.id} complete\n`);
    } catch (error) {
      evaluated.push({
        id: testCase.id,
        source: testCase.source,
        query: testCase.query,
        evaluation_version: evaluationVersion,
        retrieval_version: "ledger-retrieval.v3",
        error: error instanceof Error ? error.message : String(error),
      });
      process.stderr.write(`[${index + 1}/${queries.length}] ${testCase.id} failed\n`);
    }
  }
  const complete = evaluated.filter((item) => item.judge && item.modes);
  const qualified = complete.filter((item) => item.judge.route.score >= 2);
  const pairwise = {};
  for (const [left, right] of [["baseline", "key_only"], ["baseline", "combined"], ["key_only", "combined"]]) {
    const counts = { left: 0, ties: 0, right: 0 };
    for (const item of qualified) {
      const judgments = new Map(item.judge.judgments.map((value) => [value.candidate_key, value]));
      const leftScore = metricForResults(item.modes[left].results, judgments).ndcg_at_5;
      const rightScore = metricForResults(item.modes[right].results, judgments).ndcg_at_5;
      if (Math.abs(leftScore - rightScore) < 0.000001) counts.ties += 1;
      else if (leftScore > rightScore) counts.left += 1;
      else counts.right += 1;
    }
    pairwise[`${left} vs ${right}`] = counts;
  }
  const regressions = qualified.map((item) => {
    const judgments = new Map(item.judge.judgments.map((value) => [value.candidate_key, value]));
    const baseline = metricForResults(item.modes.baseline.results, judgments).ndcg_at_5;
    const combined = metricForResults(item.modes.combined.results, judgments).ndcg_at_5;
    return { id: item.id, query: item.query, baseline, combined, delta: combined - baseline };
  }).sort((left, right) => left.delta - right.delta).slice(0, 10);
  const highCoverage = (item) => item.sidecar_coverage?.baseline_top_5 >= 0.8;
  const lowCoverage = (item) => !highCoverage(item);
  const filingOnly = (item) => item.sidecar_coverage?.scope === "selective_filing_sample";
  const nonFiling = (item) => item.sidecar_coverage?.scope === "complete_source_sample_with_quality_gate";
  const usage = complete.reduce((total, item) => {
    const values = [
      item.judge_cache?.page?.usage || {},
      item.judge_cache?.route?.usage || {},
    ];
    for (const value of values) {
      total.prompt_tokens += Number(value.prompt_tokens || 0);
      total.completion_tokens += Number(value.completion_tokens || 0);
      total.total_tokens += Number(value.total_tokens || 0);
    }
    return total;
  }, { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 });
  const report = {
    generated_at: new Date().toISOString(),
    run_id: runId,
    base_url: baseUrl,
    evaluation_version: evaluationVersion,
    retrieval_version: "ledger-retrieval.v3",
    evaluation_design: {
      query_set: "50 frozen production-query shapes and known retrieval failures",
      router: "production routeWithModel plus shared materializeModelRoute",
      executor: "shared executeRetrievalPlan and selectContextBundle used by the browser UI",
      judgments: "separate blind route and pooled page judgments; page judging uses up to 18,000 characters of original Markdown",
      metrics: "ranked nDCG@5, precision@5, direct page@1, compiled-context precision/coverage/sufficiency, and latency",
      limitation: "Relevance is measured against the pooled visible candidates and compiled bundles, not exhaustive corpus qrels. Filing Key coverage is selective and query-centred.",
    },
    total_cases: evaluated.length,
    completed_cases: complete.length,
    pooled_evidence_cases: complete.filter((item) => item.judge.pooled_evidence_available).length,
    ranker_qualified_cases: qualified.length,
    errors: evaluated.filter((item) => item.error).map((item) => ({
      id: item.id,
      error: item.error,
    })),
    all_completed: Object.fromEntries(modes.map((mode) => [mode, aggregate(complete, mode, false)])),
    ranker_qualified: Object.fromEntries(modes.map((mode) => [mode, aggregate(complete, mode, true)])),
    coverage_segments: {
      high_candidate_coverage_cases: qualified.filter(highCoverage).length,
      low_candidate_coverage_cases: qualified.filter(lowCoverage).length,
      filing_selective_cases: qualified.filter(filingOnly).length,
      non_filing_source_sample_cases: qualified.filter(nonFiling).length,
      high_candidate_coverage: Object.fromEntries(
        modes.map((mode) => [mode, aggregate(complete, mode, true, highCoverage)]),
      ),
      low_candidate_coverage: Object.fromEntries(
        modes.map((mode) => [mode, aggregate(complete, mode, true, lowCoverage)]),
      ),
      filing_selective: Object.fromEntries(
        modes.map((mode) => [mode, aggregate(complete, mode, true, filingOnly)]),
      ),
      non_filing_source_sample: Object.fromEntries(
        modes.map((mode) => [mode, aggregate(complete, mode, true, nonFiling)]),
      ),
    },
    judge_usage: usage,
    pairwise_ndcg_wins: pairwise,
    router_failures: complete
      .filter((item) => item.judge.route.score < 2)
      .map((item) => ({
        id: item.id,
        query: item.query,
        score: item.judge.route.score,
        error_type: item.judge.route.error_type,
        notes: item.judge.route.notes,
      })),
    largest_combined_regressions: regressions,
    cases: evaluated,
  };
  const immutableReportPath = join(runRoot, "eval_report.json");
  const immutableSummaryPath = join(runRoot, "eval_report.md");
  const markdown = markdownSummary(report);
  await writeFile(immutableReportPath, `${JSON.stringify(report, null, 2)}\n`);
  await writeFile(immutableSummaryPath, markdown);
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  await writeFile(summaryPath, markdown);
  process.stdout.write(`${JSON.stringify({
    generated_at: report.generated_at,
    run_id: report.run_id,
    immutable_report: immutableReportPath,
    completed_cases: report.completed_cases,
    ranker_qualified_cases: report.ranker_qualified_cases,
    ranker_qualified: report.ranker_qualified,
    pairwise_ndcg_wins: report.pairwise_ndcg_wins,
    coverage_segments: report.coverage_segments,
    judge_usage: report.judge_usage,
    router_failures: report.router_failures,
    errors: report.errors,
  }, null, 2)}\n`);
}

await main();
