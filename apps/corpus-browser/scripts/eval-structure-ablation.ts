import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  executeRetrievalPlan,
  type AppliedSearch,
  type SearchResult,
} from "../app/lib/retrieval-execution";
import { buildResultDisplaySections } from "../app/lib/result-presentation";

interface SourceResult {
  id: string;
  query: string;
  applied: AppliedSearch | null;
}

interface SourceReport {
  generated_at: string;
  results: SourceResult[];
}

const DEFAULT_API = "https://psxgpt-corpus-api-key-eval.isfandiyar-shaheen.workers.dev";
const DEFAULT_SOURCE_REPORT = resolve(
  process.cwd(),
  "../../artifacts/corpus_browser_user_fact_search_first_turn_v1/runs/2026-07-30T17-30-01-088Z/eval_report.json",
);

function argument(name: string): string | undefined {
  return process.argv
    .find((value) => value.startsWith(`--${name}=`))
    ?.slice(name.length + 3);
}

async function apiJson(api: string, path: string): Promise<Record<string, unknown>> {
  const response = await fetch(new URL(path, api), {
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await response.json() as Record<string, unknown>;
  if (!response.ok) throw new Error(`${response.status}: ${String(payload.error || "request failed")}`);
  return payload;
}

async function retrieve(
  api: string,
  applied: AppliedSearch,
  retrievalMode: "baseline" | "structure",
) {
  const started = performance.now();
  const response = await executeRetrievalPlan(applied, {
    limit: 6,
    retrievalMode,
    debug: true,
    fetcher: (input, init) => {
      const url = new URL(String(input), api);
      url.searchParams.set("eval_nonce", crypto.randomUUID());
      return fetch(url, {
        ...init,
        signal: AbortSignal.timeout(30_000),
      });
    },
  });
  return {
    response,
    latency_ms: Math.round(performance.now() - started),
  };
}

async function visiblePages(api: string, response: Awaited<ReturnType<typeof retrieve>>["response"], applied: AppliedSearch) {
  const sections = buildResultDisplaySections(response, applied);
  const sources = [...new Map(
    sections
      .flatMap((section) => section.groups.flatMap((group) => group.sources.slice(0, 3)))
      .map((source) => [`${source.corpus}:${source.id}`, source]),
  ).values()];
  const hydrated = await Promise.all(sources.map(async (source: SearchResult) => {
    const document = await apiJson(
      api,
      `/api/document?corpus=${source.corpus}&id=${source.id}`,
    );
    return {
      corpus: source.corpus,
      id: source.id,
      entity: source.entity,
      period: source.period,
      page_number: source.page_number,
      title: source.derived_heading || source.title,
      summary: source.summary,
      snippet: source.snippet,
      retrieval_lanes: source.retrieval_lanes,
      text_excerpt: String(document.text || "").slice(0, 4_000),
    };
  }));
  const pagesById = new Map(hydrated.map((page) => [`${page.corpus}:${page.id}`, page]));
  return sections.map((section) => ({
    key: section.key,
    title: section.title,
    groups: section.groups.map((group) => ({
      label: group.label,
      pages: group.sources
        .slice(0, 3)
        .flatMap((source) => {
          const page = pagesById.get(`${source.corpus}:${source.id}`);
          return page ? [page] : [];
        }),
    })),
  }));
}

function visibleIds(sections: Awaited<ReturnType<typeof visiblePages>>): string[] {
  return sections.flatMap((section) =>
    section.groups.flatMap((group) => group.pages.map((page) => `${page.corpus}:${page.id}`))
  );
}

async function main() {
  const api = argument("api") || DEFAULT_API;
  const sourceReportPath = resolve(argument("source-report") || DEFAULT_SOURCE_REPORT);
  const source = JSON.parse(await readFile(sourceReportPath, "utf8")) as SourceReport;
  const requestedIds = new Set(process.argv.slice(2).filter((value) => !value.startsWith("--")));
  const cases = source.results.filter((item) =>
    item.applied && (requestedIds.size === 0 || requestedIds.has(item.id))
  );
  const results = [];

  for (const item of cases) {
    const baseline = await retrieve(api, item.applied!, "baseline");
    const structure = await retrieve(api, item.applied!, "structure");
    const [baselinePages, structurePages] = await Promise.all([
      visiblePages(api, baseline.response, item.applied!),
      visiblePages(api, structure.response, item.applied!),
    ]);
    const baselineIds = visibleIds(baselinePages);
    const structureIds = visibleIds(structurePages);
    const changed = JSON.stringify(baselineIds) !== JSON.stringify(structureIds);
    results.push({
      id: item.id,
      query: item.query,
      applied: item.applied,
      changed,
      baseline: {
        latency_ms: baseline.latency_ms,
        visible_ids: baselineIds,
        display_pages: baselinePages,
        diagnostics: baseline.response.diagnostics,
      },
      structure: {
        latency_ms: structure.latency_ms,
        visible_ids: structureIds,
        display_pages: structurePages,
        diagnostics: structure.response.diagnostics,
      },
    });
    process.stdout.write(
      `${changed ? "CHANGED" : "SAME"} ${item.id} · baseline ${baseline.latency_ms}ms · structure ${structure.latency_ms}ms\n`,
    );
  }

  const generatedAt = new Date().toISOString();
  const report = {
    generated_at: generatedAt,
    source_report: sourceReportPath,
    source_route_generated_at: source.generated_at,
    retrieval_api: api,
    compared_modes: ["baseline", "structure"],
    total: results.length,
    changed: results.filter((item) => item.changed).length,
    results,
  };
  const outputDir = resolve(
    process.cwd(),
    `../../artifacts/corpus_browser_structure_ablation/runs/${generatedAt.replace(/[:.]/g, "-")}`,
  );
  await mkdir(outputDir, { recursive: true });
  const outputPath = resolve(outputDir, "eval_report.json");
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`\n${report.changed}/${report.total} visible result sets changed\n${outputPath}\n`);
}

void main();
