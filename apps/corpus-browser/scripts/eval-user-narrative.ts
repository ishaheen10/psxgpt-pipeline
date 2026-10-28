import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, resolve } from "node:path";
import {
  materializeModelRoute,
  routeWithModel,
  type Entity,
  type ModelRoute,
} from "../app/api/route/route";
import {
  executeRetrievalPlan,
  type AppliedSearch,
  type CorpusKind,
  type SearchResult,
} from "../app/lib/retrieval-execution";
import {
  buildResultDisplaySections,
} from "../app/lib/result-presentation";

interface NarrativeCase {
  id: string;
  posture: string;
  query: string;
  expected_tickers: string[];
  must_include_corpora: CorpusKind[];
  minimum_facets: number;
  answer_requirements: string[];
}

interface Dataset {
  name: string;
  version: number;
  source: Record<string, string>;
  purpose: string;
  judgment_policy: Record<string, unknown>;
  cases: NarrativeCase[];
}

interface HydratedPage {
  id: number;
  corpus: CorpusKind;
  entity: string;
  period: string;
  page_number: number | null;
  title: string;
  summary: string;
  snippet: string;
  source_url: string | null;
  retrieval_facets: string[];
  document: {
    search_text: string;
    text_excerpt: string;
    quality_flags: unknown[];
  } | null;
  hydration_error: string | null;
}

const API = "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev";
const REFERENCE_DATE = "2026-07-30";
function sorted(values: string[]): string[] {
  return [...new Set(values)].sort();
}

function routeFailures(
  route: ModelRoute,
  applied: AppliedSearch | null,
  item: NarrativeCase,
): string[] {
  const failures: string[] = [];
  const actualTickers = sorted(applied?.tickers || route.entities.map((entity) => entity.ticker));
  const expectedTickers = sorted(item.expected_tickers);
  if (JSON.stringify(actualTickers) !== JSON.stringify(expectedTickers)) {
    failures.push(
      `tickers expected ${expectedTickers.join(",") || "none"}, got ${actualTickers.join(",") || "none"}`,
    );
  }
  for (const corpus of item.must_include_corpora) {
    if (!route.corpora.includes(corpus)) {
      failures.push(`route omitted required ${corpus} corpus`);
    }
  }
  if (route.search_facets.length < item.minimum_facets) {
    failures.push(
      `expected at least ${item.minimum_facets} evidence facets, got ${route.search_facets.length}`,
    );
  }
  if (route.continuation) failures.push("fresh query was classified as a continuation");
  return failures;
}

async function apiFetch(path: string): Promise<Record<string, unknown>> {
  const response = await fetch(new URL(path, API), {
    signal: AbortSignal.timeout(30_000),
  });
  const body = await response.json() as Record<string, unknown>;
  if (!response.ok) {
    throw new Error(`${response.status}: ${String(body.error || "request failed")}`);
  }
  return body;
}

async function retrieve(applied: AppliedSearch) {
  return executeRetrievalPlan(applied, {
    limit: 6,
    fetcher: (input, init) => {
      const url = new URL(String(input), API);
      url.searchParams.set("eval_nonce", crypto.randomUUID());
      return fetch(url, {
        ...init,
        signal: AbortSignal.timeout(30_000),
      });
    },
  });
}

async function hydratePage(source: SearchResult): Promise<HydratedPage> {
  let document: HydratedPage["document"] = null;
  let hydrationError: string | null = null;
  try {
    const payload = await apiFetch(
      `/api/document?corpus=${encodeURIComponent(source.corpus)}&id=${source.id}`,
    );
    document = {
      search_text: String(payload.search_text || ""),
      text_excerpt: String(payload.text || "").slice(0, 8_000),
      quality_flags: Array.isArray(payload.quality_flags) ? payload.quality_flags : [],
    };
  } catch (error) {
    hydrationError = error instanceof Error ? error.message : "Unknown hydration error";
  }
  return {
    id: source.id,
    corpus: source.corpus,
    entity: source.entity,
    period: source.period,
    page_number: source.page_number,
    title: source.derived_heading || source.title,
    summary: source.summary,
    snippet: source.snippet,
    source_url: source.source_url,
    retrieval_facets: source.retrieval_facets || [],
    document,
    hydration_error: hydrationError,
  };
}

function percentile(values: number[], fraction: number): number {
  if (values.length === 0) return 0;
  const ordered = [...values].sort((left, right) => left - right);
  return ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * fraction))];
}

async function main() {
  if (!process.env.GEMINI_API_KEY) throw new Error("GEMINI_API_KEY is required.");
  const args = process.argv.slice(2);
  const requestedIds = new Set(args.filter((arg) => !arg.startsWith("--")));
  const requestedModel = args.find((arg) => arg.startsWith("--model="))?.slice("--model=".length);
  const requestedDataset = args
    .find((arg) => arg.startsWith("--dataset="))
    ?.slice("--dataset=".length);
  const datasetPath = resolve(
    process.cwd(),
    requestedDataset || "evals/user-narrative-first-turn-v1.json",
  );
  const dataset = JSON.parse(await readFile(datasetPath, "utf8")) as Dataset;
  const activeCases = requestedIds.size
    ? dataset.cases.filter((item) => requestedIds.has(item.id))
    : dataset.cases;
  if (activeCases.length === 0) throw new Error("No eval case matched the requested ids.");

  const directoryPayload = await apiFetch("/api/entities") as unknown as { entities: Entity[] };
  const results = [];
  for (const item of activeCases) {
    const started = Date.now();
    const directoryTickers = new Set(directoryPayload.entities.map((entity) => entity.ticker));
    const directoryMissingTickers = item.expected_tickers.filter(
      (ticker) => !directoryTickers.has(ticker),
    );
    try {
      const routeStarted = Date.now();
      const route = await routeWithModel(
        item.query,
        directoryPayload.entities,
        REFERENCE_DATE,
        null,
        {
          model: requestedModel,
          reasoningEffort: "medium",
        },
      );
      const routeLatencyMs = Date.now() - routeStarted;
      let applied: AppliedSearch | null = null;
      let materializationError: string | null = null;
      try {
        applied = materializeModelRoute(item.query, route, directoryPayload.entities, null);
      } catch (error) {
        materializationError = error instanceof Error
          ? error.message
          : "Unknown materialization error";
      }
      const failures = routeFailures(route, applied, item);
      if (materializationError) failures.push(materializationError);

      let retrieval = null;
      let retrievalError: string | null = null;
      let topPages: HydratedPage[] = [];
      let displaySections: ReturnType<typeof buildResultDisplaySections> = [];
      let displayPages: Array<{
        key: string;
        eyebrow: "Company" | "Evidence need";
        title: string;
        groups: Array<{ label: string; pages: HydratedPage[] }>;
      }> = [];
      let retrievalLatencyMs: number | null = null;
      let hydrationLatencyMs: number | null = null;
      if (applied) {
        try {
          const retrievalStarted = Date.now();
          retrieval = await retrieve(applied);
          retrievalLatencyMs = Date.now() - retrievalStarted;
          displaySections = buildResultDisplaySections(retrieval, applied);
          const hydrationStarted = Date.now();
          const visibleSources = displaySections.flatMap((section) =>
            section.groups.flatMap((group) => group.sources.slice(0, 3))
          );
          const uniqueVisibleSources = [...new Map(
            visibleSources.map((source) => [`${source.corpus}:${source.id}`, source]),
          ).values()];
          const hydratedVisiblePages = await Promise.all(uniqueVisibleSources.map(hydratePage));
          const pagesBySource = new Map(
            hydratedVisiblePages.map((page) => [`${page.corpus}:${page.id}`, page]),
          );
          displayPages = displaySections.map((section) => ({
            key: section.key,
            eyebrow: section.eyebrow,
            title: section.title,
            groups: section.groups.map((group) => ({
              label: group.label,
              pages: group.sources
                .slice(0, 3)
                .flatMap((source) => {
                  const page = pagesBySource.get(`${source.corpus}:${source.id}`);
                  return page ? [page] : [];
                }),
            })),
          }));
          topPages = displayPages
            .flatMap((section) => section.groups.flatMap((group) => group.pages))
            .slice(0, 3);
          hydrationLatencyMs = Date.now() - hydrationStarted;
        } catch (error) {
          retrievalError = error instanceof Error ? error.message : "Unknown retrieval error";
        }
      }
      const latencyMs = Date.now() - started;
      const pass = failures.length === 0 && retrievalError === null;
      results.push({
        id: item.id,
        posture: item.posture,
        query: item.query,
        answer_requirements: item.answer_requirements,
        route_contract_pass: pass,
        route_failures: failures,
        directory_missing_tickers: directoryMissingTickers,
        materialization_error: materializationError,
        retrieval_error: retrievalError,
        latency_ms: latencyMs,
        route_latency_ms: routeLatencyMs,
        retrieval_latency_ms: retrievalLatencyMs,
        hydration_latency_ms: hydrationLatencyMs,
        route,
        applied,
        display_pages: displayPages,
        display_sections: displaySections.map((section) => ({
          key: section.key,
          eyebrow: section.eyebrow,
          title: section.title,
          groups: section.groups.map((group) => ({
            label: group.label,
            sources: group.sources.slice(0, 3),
          })),
        })),
        top_pages: topPages,
        retrieval,
      });
      const pageLine = topPages
        .map((page, index) => `${index + 1}:${page.entity || page.corpus}/${page.title}`)
        .join(" | ");
      process.stdout.write(
        `${pass ? "PASS" : "FAIL"} ${item.id} (${latencyMs}ms)${pageLine ? ` — ${pageLine}` : ""}\n`,
      );
    } catch (error) {
      const latencyMs = Date.now() - started;
      results.push({
        id: item.id,
        posture: item.posture,
        query: item.query,
        answer_requirements: item.answer_requirements,
        route_contract_pass: false,
        route_failures: [error instanceof Error ? error.message : "Unknown eval error"],
        directory_missing_tickers: directoryMissingTickers,
        retrieval_error: null,
        latency_ms: latencyMs,
        route_latency_ms: latencyMs,
        retrieval_latency_ms: null,
        hydration_latency_ms: null,
        route: null,
        applied: null,
        display_pages: [],
        display_sections: [],
        top_pages: [],
        retrieval: null,
      });
      process.stdout.write(`FAIL ${item.id} (${latencyMs}ms)\n`);
    }
  }

  const latencies = results.map((result) => result.latency_ms);
  const passed = results.filter((result) => result.route_contract_pass).length;
  const generatedAt = new Date().toISOString();
  const report = {
    generated_at: generatedAt,
    reference_date: REFERENCE_DATE,
    dataset: {
      name: dataset.name,
      version: dataset.version,
      source: dataset.source,
      purpose: dataset.purpose,
      judgment_policy: dataset.judgment_policy,
    },
    planner: "production routeWithModel",
    model: requestedModel || "gemini-3.5-flash-lite",
    reasoning_effort: "medium",
    retrieval_api: API,
    total: results.length,
    route_contract_passed: passed,
    route_contract_failed: results.length - passed,
    median_latency_ms: percentile(latencies, 0.5),
    p90_latency_ms: percentile(latencies, 0.9),
    results,
  };
  const runId = generatedAt.replace(/[:.]/g, "-");
  const reportDir = resolve(
    process.cwd(),
    `../../artifacts/corpus_browser_${basename(datasetPath, ".json").replaceAll("-", "_")}/runs`,
    runId,
  );
  await mkdir(reportDir, { recursive: true });
  const outputPath = resolve(reportDir, "eval_report.json");
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  process.stdout.write(
    `\n${passed}/${results.length} route contracts passed`
      + `; median ${report.median_latency_ms}ms; p90 ${report.p90_latency_ms}ms\n`
      + `${outputPath}\n`,
  );
  if (passed !== results.length) process.exitCode = 1;
}

void main();
