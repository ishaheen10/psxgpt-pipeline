import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import {
  routeWithModel,
  SearchRouterError,
  type Entity,
  type ModelRoute,
} from "../../apps/corpus-browser/app/api/route/route";

type CorpusKind = "filing" | "announcement" | "macro";

interface PriorContext {
  naturalQuery: string;
  tickers: string[];
  industries: string[];
  years: number[];
  corpora: CorpusKind[];
  filingCadence: "annual" | "quarterly" | "any";
  latest: boolean;
}

interface ConceptExpectation {
  corpora: CorpusKind[];
  key_concepts: string[][];
}

interface EntityOrKeyExpectation {
  entities: string[];
  key_concepts: string[][];
}

interface ExpectedRoute {
  entities?: string[];
  optional_entities?: string[];
  excluded_entities?: string[];
  industries?: string[];
  corpora?: CorpusKind[];
  temporal_mode?: ModelRoute["temporal_mode"];
  filing_cadence?: ModelRoute["filing_cadence"] | ModelRoute["filing_cadence"][];
  workflow?: ModelRoute["workflow"];
  facet_count?: number | number[];
  key_concepts?: string[][];
  facet_expectations?: ConceptExpectation[];
  entity_or_key?: EntityOrKeyExpectation[];
}

interface RouterCase {
  id: string;
  smoke: boolean;
  source: string;
  query: string;
  prior?: PriorContext;
  expected: ExpectedRoute;
}

const PROJECT_ROOT = resolve(import.meta.dirname, "../..");
const CASE_PATH = resolve(import.meta.dirname, "router_cases.jsonl");
const API = "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev";
const REFERENCE_DATE = "2026-07-29";
const GENERIC_KEY_WORDS = new Set([
  "annual", "announcement", "document", "filing", "information", "page",
  "quarterly", "report", "statement",
]);

function normalizedWords(value: string): string[] {
  return (value.normalize("NFKC").toLowerCase().match(/[a-z0-9]+/g) || []);
}

function sameSet(left: string[], right: string[]): boolean {
  return JSON.stringify([...new Set(left)].sort()) === JSON.stringify([...new Set(right)].sort());
}

function keyFailures(
  keyQuery: string,
  concepts: string[][] | undefined,
  forbidden: Set<string>,
  label: string,
): string[] {
  const failures: string[] = [];
  const words = normalizedWords(keyQuery);
  if (words.length === 0 || words.length > 4) {
    failures.push(`${label} key query must contain one to four words, got ${words.length}`);
  }
  const forbiddenMatches = words.filter((word) => GENERIC_KEY_WORDS.has(word) || forbidden.has(word));
  if (forbiddenMatches.length) {
    failures.push(`${label} key query contains filter/document terms: ${forbiddenMatches.join(", ")}`);
  }
  for (const alternatives of concepts || []) {
    const matches = alternatives.some((term) => {
      const expected = term.toLowerCase();
      return words.some((word) =>
        word === expected
        || (
          word.length >= 4
          && expected.length >= 4
          && Math.abs(word.length - expected.length) <= 3
          && (word.startsWith(expected) || expected.startsWith(word))
        )
      );
    });
    if (!matches) {
      failures.push(`${label} key query misses concept group ${alternatives.join("|")}`);
    }
  }
  return failures;
}

function evaluateRoute(item: RouterCase, route: ModelRoute): string[] {
  const failures: string[] = [];
  const expected = item.expected;
  const entities = route.entities.map((entity) => entity.ticker);
  if (expected.entities) {
    const missing = expected.entities.filter((ticker) => !entities.includes(ticker));
    const allowed = new Set([...expected.entities, ...(expected.optional_entities || [])]);
    const unexpected = entities.filter((ticker) => !allowed.has(ticker));
    if (missing.length || unexpected.length) {
      failures.push(
        `entities required ${expected.entities.join(",") || "none"}`
        + `${expected.optional_entities?.length ? `; optional ${expected.optional_entities.join(",")}` : ""}`
        + `; got ${entities.join(",") || "none"}`,
      );
    }
  }
  for (const entity of expected.excluded_entities || []) {
    if (entities.includes(entity)) failures.push(`ordinary term resolved as entity ${entity}`);
  }
  if (expected.industries) {
    const actual = route.industries.map((value) => value.toLowerCase());
    for (const industry of expected.industries) {
      if (!actual.some((value) => value.includes(industry.toLowerCase()))) {
        failures.push(`missing industry ${industry}; got ${actual.join(",") || "none"}`);
      }
    }
  }
  if (expected.corpora && !sameSet(route.corpora, expected.corpora)) {
    failures.push(`corpora expected ${expected.corpora.join(",")}, got ${route.corpora.join(",")}`);
  }
  if (expected.temporal_mode && route.temporal_mode !== expected.temporal_mode) {
    failures.push(`temporal_mode expected ${expected.temporal_mode}, got ${route.temporal_mode}`);
  }
  const acceptedCadences = Array.isArray(expected.filing_cadence)
    ? expected.filing_cadence
    : expected.filing_cadence
      ? [expected.filing_cadence]
      : [];
  if (acceptedCadences.length && !acceptedCadences.includes(route.filing_cadence)) {
    failures.push(`filing_cadence expected ${acceptedCadences.join(" or ")}, got ${route.filing_cadence}`);
  }
  if (expected.workflow && route.workflow !== expected.workflow) {
    failures.push(`workflow expected ${expected.workflow}, got ${route.workflow}`);
  }

  const effectiveFacets = route.search_facets.length
    ? route.search_facets
    : [{
        label: "Primary evidence",
        field_queries: route.field_queries,
        corpora: route.corpora,
      }];
  const acceptedFacetCounts = Array.isArray(expected.facet_count)
    ? expected.facet_count
    : expected.facet_count
      ? [expected.facet_count]
      : [];
  if (acceptedFacetCounts.length && !acceptedFacetCounts.includes(effectiveFacets.length)) {
    failures.push(`facets expected ${acceptedFacetCounts.join(" or ")}, got ${effectiveFacets.length}`);
  }
  const forbidden = new Set([
    ...entities.map((value) => value.toLowerCase()),
    ...normalizedWords(item.query).filter((word) => /^\d{4}$/.test(word)),
  ]);
  if (expected.facet_expectations) {
    expected.facet_expectations.forEach((facetExpected, index) => {
      const facet = effectiveFacets[index];
      if (!facet) return;
      if (!sameSet(facet.corpora, facetExpected.corpora)) {
        failures.push(`facet ${index + 1} corpora expected ${facetExpected.corpora.join(",")}, got ${facet.corpora.join(",")}`);
      }
      failures.push(...keyFailures(
        facet.field_queries.key,
        facetExpected.key_concepts,
        forbidden,
        `facet ${index + 1}`,
      ));
    });
    const keys = effectiveFacets.map((facet) => normalizedWords(facet.field_queries.key).join(" "));
    if (new Set(keys).size !== keys.length) failures.push("multi-facet plan reused the same key query");
  } else {
    for (const [index, facet] of effectiveFacets.entries()) {
      failures.push(...keyFailures(
        facet.field_queries.key,
        undefined,
        forbidden,
        effectiveFacets.length === 1 ? "primary" : `facet ${index + 1}`,
      ));
    }
    const combinedKey = effectiveFacets.map((facet) => facet.field_queries.key).join(" ");
    failures.push(...keyFailures(
      combinedKey,
      expected.key_concepts,
      new Set(),
      "combined facet",
    ).filter((failure) => !failure.includes("must contain one to four words")));
  }
  for (const alternative of expected.entity_or_key || []) {
    const matchedEntity = alternative.entities.some((ticker) => entities.includes(ticker));
    if (matchedEntity) continue;
    const alternativeFailures = keyFailures(
      effectiveFacets.map((facet) => facet.field_queries.key).join(" "),
      alternative.key_concepts,
      new Set(),
      "entity-or-key",
    ).filter((failure) => !failure.includes("must contain one to four words"));
    failures.push(...alternativeFailures);
  }
  return failures;
}

async function loadCases(): Promise<RouterCase[]> {
  return (await readFile(CASE_PATH, "utf8"))
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line) as RouterCase);
}

async function main() {
  if (!process.env.GEMINI_API_KEY) throw new Error("GEMINI_API_KEY is required");
  const argv = process.argv.slice(2);
  const args = new Set(argv);
  const repeatsArgument = argv.find((value) => value.startsWith("--repeats="));
  const repeats = Math.max(1, Math.min(5, Number(repeatsArgument?.split("=")[1] || 1) || 1));
  const modelArgument = argv.find((value) => value.startsWith("--model="));
  const model = modelArgument?.slice("--model=".length) || "gemini-3.5-flash-lite";
  const reasoningArgument = argv.find((value) => value.startsWith("--reasoning="));
  const reasoningEffort = reasoningArgument?.slice("--reasoning=".length) as
    | "minimal"
    | "low"
    | "medium"
    | "high"
    | undefined;
  const timeoutMs = model === "gemini-3.5-flash-lite" ? 5_500 : 10_000;
  const allCases = await loadCases();
  const activeCases = args.has("--all")
    ? allCases
    : allCases.filter((item) => item.smoke);
  const directoryResponse = await fetch(`${API}/api/entities`, {
    signal: AbortSignal.timeout(15_000),
  });
  if (!directoryResponse.ok) throw new Error(`Entity directory HTTP ${directoryResponse.status}`);
  const directory = await directoryResponse.json() as { entities: Entity[] };
  const results = [];
  for (let repeat = 1; repeat <= repeats; repeat += 1) {
    for (const item of activeCases) {
      const started = performance.now();
      try {
        const route = await routeWithModel(
          item.query,
          directory.entities,
          REFERENCE_DATE,
          item.prior || null,
          { model, reasoningEffort: reasoningEffort || "low", timeoutMs },
        );
        const failures = evaluateRoute(item, route);
        results.push({
          repeat,
          id: item.id,
          query: item.query,
          source: item.source,
          pass: failures.length === 0,
          failures,
          latency_ms: Math.round(performance.now() - started),
          expected: item.expected,
          route,
        });
        process.stdout.write(`${failures.length ? "FAIL" : "PASS"} r${repeat} ${item.id}${failures.length ? ` — ${failures.join("; ")}` : ""}\n`);
      } catch (error) {
        const detail = error instanceof SearchRouterError
          ? error.detail
          : error instanceof Error
            ? error.message
            : "unknown router error";
        results.push({
          repeat,
          id: item.id,
          query: item.query,
          source: item.source,
          pass: false,
          failures: [detail],
          latency_ms: Math.round(performance.now() - started),
          expected: item.expected,
          route: null,
        });
        process.stdout.write(`FAIL r${repeat} ${item.id} — ${detail}\n`);
      }
    }
  }
  const latencies = results.map((row) => row.latency_ms).sort((a, b) => a - b);
  const passed = results.filter((row) => row.pass).length;
  const caseResults = activeCases.map((item) => {
    const runs = results.filter((row) => row.id === item.id);
    const passedRuns = runs.filter((row) => row.pass).length;
    return {
      id: item.id,
      passed_runs: passedRuns,
      total_runs: runs.length,
      pass_rate: runs.length ? passedRuns / runs.length : 0,
      stable_pass: passedRuns === runs.length,
    };
  });
  const caseHash = createHash("sha256")
    .update(await readFile(CASE_PATH))
    .digest("hex");
  const routerSourceHash = createHash("sha256")
    .update(await readFile(resolve(PROJECT_ROOT, "apps/corpus-browser/app/api/route/route.ts")))
    .digest("hex");
  const report = {
    generated_at: new Date().toISOString(),
    stage: args.has("--all") ? "full" : "smoke",
    model,
    reasoning_effort: reasoningEffort || "low",
    reference_date: REFERENCE_DATE,
    repeats,
    case_sha256: caseHash,
    router_source_sha256: routerSourceHash,
    total_cases: activeCases.length,
    stable_passed_cases: caseResults.filter((row) => row.stable_pass).length,
    total_runs: results.length,
    passed_runs: passed,
    failed_runs: results.length - passed,
    run_pass_rate: results.length ? passed / results.length : 0,
    median_latency_ms: latencies[Math.floor(latencies.length / 2)] || 0,
    p90_latency_ms: latencies[Math.floor(latencies.length * 0.9)] || 0,
    case_results: caseResults,
    results,
  };
  const reportDir = resolve(PROJECT_ROOT, "artifacts/reports");
  await mkdir(reportDir, { recursive: true });
  const outputPath = resolve(
    reportDir,
    `page_key_router_${report.stage}_${model.replaceAll(/[^a-z0-9]+/gi, "_")}${repeats > 1 ? "_stability" : ""}.json`,
  );
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  process.stdout.write(
    `\n${passed}/${results.length} runs passed; ${report.stable_passed_cases}/${activeCases.length} cases stable\n${outputPath}\n`,
  );
  if (passed !== results.length) process.exitCode = 1;
}

void main();
