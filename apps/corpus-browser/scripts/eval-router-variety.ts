import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  materializeModelRoute,
  routeWithModel,
  type Entity,
  type ModelRoute,
} from "../app/api/route/route";
import {
  executeRetrievalPlan,
  type AppliedSearch,
} from "../app/lib/retrieval-execution";

type CorpusKind = "filing" | "announcement" | "macro";

interface Expectation {
  tickers?: string[];
  requiredTickers?: string[];
  supportingTickers?: string[];
  excludedTickers?: string[];
  corpora?: CorpusKind[];
  includedCorpora?: CorpusKind[];
  temporalMode?: ModelRoute["temporal_mode"];
  cadence?: ModelRoute["filing_cadence"];
  workflow?: ModelRoute["workflow"];
  explicitCorpus?: CorpusKind;
  minimumFacets?: number;
  unresolved?: boolean;
  industry?: string;
  minimumAnnualFacets?: number;
  exactFacets?: number;
}

interface EvalCase {
  id: string;
  query: string;
  expected: Expectation;
  prior?: {
    naturalQuery: string;
    tickers: string[];
    industries: string[];
    years: number[];
    corpora: CorpusKind[];
    filingCadence: "annual" | "quarterly" | "any";
    latest: boolean;
  };
}

const API = "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev";
const REFERENCE_DATE = "2026-07-29";
const cases: EvalCase[] = [
  {
    id: "announcement_event_plain_language",
    query: "UBL insider purchases and sales",
    expected: {
      tickers: ["UBL"],
      corpora: ["announcement"],
      temporalMode: "all",
      cadence: "any",
      workflow: "browse",
    },
  },
  {
    id: "latest_measurement_unspecified_cadence",
    query: "HBL latest cash balance",
    expected: { tickers: ["HBL"], temporalMode: "latest", cadence: "any" },
  },
  {
    id: "latest_explicit_quarter",
    query: "HBL latest quarterly cash and bank balances",
    expected: { tickers: ["HBL"], temporalMode: "latest", cadence: "quarterly" },
  },
  {
    id: "latest_explicit_annual",
    query: "HBL latest annual capital adequacy ratio",
    expected: { tickers: ["HBL"], temporalMode: "latest", cadence: "annual" },
  },
  {
    id: "peer_multifacet_notes",
    query: "Sector level exposure of UBL HBL ABL and MCB. Include key capital ratios",
    expected: {
      tickers: ["UBL", "HBL", "ABL", "MCB"],
      temporalMode: "all",
      minimumFacets: 2,
    },
  },
  {
    id: "named_company_group",
    query: "latest tier 1 capital ratios for the big four banks",
    expected: {
      tickers: [],
      unresolved: true,
      temporalMode: "latest",
    },
  },
  {
    id: "transaction_entity_roles",
    query: "What did Maple Leaf disclose about the PIOC acquisition?",
    expected: {
      tickers: ["MLCF", "PIOC"],
      requiredTickers: ["MLCF"],
      supportingTickers: ["PIOC"],
      corpora: ["announcement"],
    },
  },
  {
    id: "financial_model_workflow",
    query: "Build a financial model for UBL using the last 3 completed years",
    expected: {
      tickers: ["UBL"],
      temporalMode: "bounded",
      cadence: "annual",
      workflow: "financial_model",
      minimumFacets: 2,
    },
  },
  {
    id: "explicit_announcement_source",
    query: "Find PSX announcements for UBL dividends in 2025",
    expected: {
      tickers: ["UBL"],
      temporalMode: "bounded",
      explicitCorpus: "announcement",
    },
  },
  {
    id: "announcement_title_language",
    query: "Has MCB announced any board meeting or closed period?",
    expected: { tickers: ["MCB"], corpora: ["announcement"], temporalMode: "all" },
  },
  {
    id: "explicit_macro_publication",
    query: "Agriculture growth in the latest Pakistan Economic Survey",
    expected: { temporalMode: "latest", explicitCorpus: "macro" },
  },
  {
    id: "macro_topic_without_named_source",
    query: "Pakistan inflation and policy rates since 2021",
    expected: { temporalMode: "bounded", cadence: "any" },
  },
  {
    id: "industry_catalog_scope",
    query: "Compare installed capacity and utilisation across the cement industry",
    expected: { temporalMode: "all", industry: "cement" },
  },
  {
    id: "full_company_name",
    query: "What is Meezan Bank's deposit mix?",
    expected: { tickers: ["MEBL"], temporalMode: "all" },
  },
  {
    id: "ticker_as_common_word",
    query: "How are fuel prices affecting power generation margins?",
    expected: { excludedTickers: ["POWER"], temporalMode: "all" },
  },
  {
    id: "specific_financial_note",
    query: "Find the deposits and other accounts note for HMB 2024",
    expected: { tickers: ["HMB"], temporalMode: "bounded" },
  },
  {
    id: "product_segment",
    query: "For the past five years show NRL base oil and other segment breakdown",
    expected: { tickers: ["NRL"], temporalMode: "bounded" },
  },
  {
    id: "governance_evidence",
    query: "Which directors of LUCK attended the fewest board meetings?",
    expected: { tickers: ["LUCK"], temporalMode: "all" },
  },
  {
    id: "accounting_policy",
    query: "How does UBL measure expected credit losses and stage 3 assets?",
    expected: { tickers: ["UBL"], temporalMode: "all" },
  },
  {
    id: "unresolved_company",
    query: "Find the latest annual report for United Bunk Pakistan",
    expected: { tickers: [], unresolved: true, temporalMode: "latest", cadence: "annual" },
  },
  {
    id: "continuation",
    query: "And what about its capital adequacy?",
    prior: {
      naturalQuery: "Show UBL sector exposure",
      tickers: ["UBL"],
      industries: [],
      years: [],
      corpora: ["filing", "announcement", "macro"],
      filingCadence: "any",
      latest: false,
    },
    expected: { tickers: ["UBL"], temporalMode: "all" },
  },
  {
    id: "cross_corpus_ambiguous_source",
    query: "What changed in ABL's dividend policy and recent payouts?",
    expected: { tickers: ["ABL"], temporalMode: "recent", minimumFacets: 2 },
  },
  {
    id: "operating_kpi",
    query: "Capacity and actual production for LUCK CHCC FCCL and MLCF",
    expected: { tickers: ["LUCK", "CHCC", "FCCL", "MLCF"], temporalMode: "all" },
  },
  {
    id: "exact_line_item",
    query: "Compare marketing and publicity expense for UBL MEBL and FABL",
    expected: { tickers: ["UBL", "MEBL", "FABL"], temporalMode: "all" },
  },
  {
    id: "narrative_risk",
    query: "What risks does NRL management identify around refinery margins?",
    expected: { tickers: ["NRL"], temporalMode: "all" },
  },
  {
    id: "named_macro_chapter",
    query: "Show the fiscal development chapter of Economic Survey 2024-25",
    expected: { temporalMode: "bounded", explicitCorpus: "macro" },
  },
  {
    id: "insider_event_paraphrase",
    query: "Show purchases and sales of UBL shares by its directors and executives",
    expected: {
      tickers: ["UBL"],
      corpora: ["announcement"],
      temporalMode: "all",
      cadence: "any",
      exactFacets: 1,
    },
  },
  {
    id: "insider_event_recent_paraphrase",
    query: "Any recent UBL director share dealings?",
    expected: {
      tickers: ["UBL"],
      corpora: ["announcement"],
      temporalMode: "recent",
      cadence: "any",
      exactFacets: 1,
    },
  },
  {
    id: "latest_bank_cash_source_language",
    query: "What are HBL's most recent balances with treasury banks?",
    expected: {
      tickers: ["HBL"],
      corpora: ["filing"],
      temporalMode: "latest",
      cadence: "any",
      exactFacets: 1,
    },
  },
  {
    id: "annual_bank_cash_explicit_source",
    query: "Show HBL cash and balances with treasury banks from its 2025 annual report",
    expected: {
      tickers: ["HBL"],
      corpora: ["filing"],
      temporalMode: "bounded",
      cadence: "annual",
      exactFacets: 1,
    },
  },
  {
    id: "latest_bank_cash_composition",
    query: "How is HBL's latest cash balance composed?",
    expected: {
      tickers: ["HBL"],
      corpora: ["filing"],
      temporalMode: "latest",
      cadence: "any",
      exactFacets: 1,
    },
  },
  {
    id: "peer_sector_exposure_paraphrase",
    query: "Compare industry-wise credit exposure for UBL, HBL, ABL and MCB",
    expected: {
      tickers: ["UBL", "HBL", "ABL", "MCB"],
      corpora: ["filing"],
      temporalMode: "all",
      cadence: "annual",
      exactFacets: 1,
    },
  },
  {
    id: "peer_sector_and_capital_paraphrase",
    query: "For UBL HBL ABL and MCB, find sector-wise advances plus CET1 and total capital ratios",
    expected: {
      tickers: ["UBL", "HBL", "ABL", "MCB"],
      corpora: ["filing"],
      temporalMode: "all",
      cadence: "annual",
      exactFacets: 2,
    },
  },
  {
    id: "transaction_notice_only_paraphrase",
    query: "Find MLCF's notice about acquiring PIOC",
    expected: {
      tickers: ["MLCF", "PIOC"],
      requiredTickers: ["MLCF"],
      supportingTickers: ["PIOC"],
      corpora: ["announcement"],
      temporalMode: "all",
      exactFacets: 1,
    },
  },
  {
    id: "transaction_filing_treatment",
    query: "How did MLCF account for PIOC after the acquisition in its annual report?",
    expected: {
      tickers: ["MLCF", "PIOC"],
      requiredTickers: ["MLCF"],
      supportingTickers: ["PIOC"],
      corpora: ["filing"],
      temporalMode: "all",
      cadence: "annual",
      exactFacets: 1,
    },
  },
  {
    id: "transaction_mixed_evidence",
    query: "What did MLCF announce about PIOC and how did the acquisition affect its latest annual financials?",
    expected: {
      tickers: ["MLCF", "PIOC"],
      requiredTickers: ["MLCF"],
      supportingTickers: ["PIOC"],
      includedCorpora: ["announcement", "filing"],
      temporalMode: "latest",
      exactFacets: 2,
    },
  },
  {
    id: "macro_food_inflation_paraphrase",
    query: "What has driven food inflation in Pakistan since 2022?",
    expected: {
      tickers: [],
      corpora: ["macro"],
      temporalMode: "bounded",
      cadence: "any",
      exactFacets: 1,
    },
  },
  {
    id: "macro_agriculture_chapter_paraphrase",
    query: "Agriculture contribution to GDP in the latest Economic Survey",
    expected: {
      tickers: [],
      corpora: ["macro"],
      temporalMode: "latest",
      cadence: "any",
      explicitCorpus: "macro",
      exactFacets: 1,
    },
  },
  {
    id: "explicit_model_components",
    query: "I want to model HBL: pull its income statement, balance sheet and cash flow for 2023 to 2025",
    expected: {
      tickers: ["HBL"],
      corpora: ["filing"],
      temporalMode: "bounded",
      cadence: "annual",
      workflow: "financial_model",
      exactFacets: 3,
    },
  },
  {
    id: "primary_statement_latest",
    query: "Show HBL's latest cash flow statement",
    expected: {
      tickers: ["HBL"],
      corpora: ["filing"],
      temporalMode: "latest",
      exactFacets: 1,
    },
  },
  {
    id: "accounting_policy_paraphrase",
    query: "What assumptions and stages does UBL use to calculate expected credit losses?",
    expected: {
      tickers: ["UBL"],
      corpora: ["filing"],
      temporalMode: "all",
      minimumFacets: 1,
    },
  },
  {
    id: "industry_capacity_question",
    query: "Which cement companies are operating closest to full production capacity?",
    expected: {
      tickers: [],
      corpora: ["filing"],
      temporalMode: "all",
      cadence: "annual",
      industry: "cement",
      exactFacets: 1,
    },
  },
  {
    id: "peer_governance_question",
    query: "Compare board-meeting attendance for LUCK and CHCC directors",
    expected: {
      tickers: ["LUCK", "CHCC"],
      corpora: ["filing"],
      temporalMode: "all",
      cadence: "annual",
      exactFacets: 1,
    },
  },
  {
    id: "announcement_board_meeting_paraphrase",
    query: "Find MCB notices scheduling board meetings",
    expected: {
      tickers: ["MCB"],
      corpora: ["announcement"],
      temporalMode: "all",
      cadence: "any",
      exactFacets: 1,
    },
  },
];

function sorted(values: string[]): string[] {
  return [...new Set(values)].sort();
}

function planFailures(
  route: ModelRoute,
  applied: AppliedSearch | null,
  expected: Expectation,
): string[] {
  const failures: string[] = [];
  const appliedEntities = applied?.entities || route.entities;
  if (expected.tickers) {
    const actual = sorted((applied?.tickers || route.entities.map((entity) => entity.ticker)));
    const wanted = sorted(expected.tickers);
    if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
      failures.push(`tickers expected ${wanted.join(",") || "none"}, got ${actual.join(",") || "none"}`);
    }
  }
  if (expected.requiredTickers) {
    const actual = sorted(
      appliedEntities
        .filter((entity) => entity.role === "required")
        .map((entity) => entity.ticker),
    );
    const wanted = sorted(expected.requiredTickers);
    if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
      failures.push(`required tickers expected ${wanted.join(",")}, got ${actual.join(",")}`);
    }
  }
  if (expected.supportingTickers) {
    const actual = sorted(
      appliedEntities
        .filter((entity) => entity.role === "supporting")
        .map((entity) => entity.ticker),
    );
    const wanted = sorted(expected.supportingTickers);
    if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
      failures.push(`supporting tickers expected ${wanted.join(",")}, got ${actual.join(",")}`);
    }
  }
  if (expected.corpora) {
    const actual = sorted(route.corpora);
    const wanted = sorted(expected.corpora);
    if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
      failures.push(`corpora expected ${wanted.join(",")}, got ${actual.join(",")}`);
    }
  }
  for (const corpus of expected.includedCorpora || []) {
    if (!route.corpora.includes(corpus)) {
      failures.push(`expected corpora to include ${corpus}`);
    }
  }
  for (const ticker of expected.excludedTickers || []) {
    if ((applied?.tickers || route.entities.map((entity) => entity.ticker)).includes(ticker)) {
      failures.push(`ordinary topic word was resolved as ticker ${ticker}`);
    }
  }
  if (expected.temporalMode && route.temporal_mode !== expected.temporalMode) {
    failures.push(`temporal_mode expected ${expected.temporalMode}, got ${route.temporal_mode}`);
  }
  if (expected.cadence && route.filing_cadence !== expected.cadence) {
    failures.push(`filing_cadence expected ${expected.cadence}, got ${route.filing_cadence}`);
  }
  if (expected.workflow && route.workflow !== expected.workflow) {
    failures.push(`workflow expected ${expected.workflow}, got ${route.workflow}`);
  }
  if (expected.explicitCorpus) {
    if (route.corpus_mode !== "explicit" || !route.corpora.includes(expected.explicitCorpus)) {
      failures.push(`expected explicit ${expected.explicitCorpus} corpus`);
    }
  }
  if (expected.minimumFacets && route.search_facets.length < expected.minimumFacets) {
    failures.push(`expected at least ${expected.minimumFacets} facets, got ${route.search_facets.length}`);
  }
  if (expected.exactFacets !== undefined && route.search_facets.length !== expected.exactFacets) {
    failures.push(`expected exactly ${expected.exactFacets} facets, got ${route.search_facets.length}`);
  }
  if (expected.unresolved && route.unresolved_entities.length === 0) {
    failures.push("expected an unresolved company mention");
  }
  if (expected.unresolved && applied) {
    failures.push("expected unresolved scope to fail closed rather than execute retrieval");
  }
  if (expected.industry) {
    const wanted = expected.industry.toLowerCase();
    if (!route.industries.some((industry) => industry.toLowerCase().includes(wanted))) {
      failures.push(`expected industry containing ${expected.industry}`);
    }
  }
  if (
    expected.minimumAnnualFacets
    && route.search_facets.filter((facet) => facet.filing_cadence === "annual").length
      < expected.minimumAnnualFacets
  ) {
    failures.push(`expected at least ${expected.minimumAnnualFacets} annual facets`);
  }
  return failures;
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

async function main() {
  if (!process.env.GEMINI_API_KEY) throw new Error("GEMINI_API_KEY is required.");
  const args = process.argv.slice(2);
  const requestedModel = args.find((arg) => arg.startsWith("--model="))?.slice("--model=".length);
  const requestedReasoning = args
    .find((arg) => arg.startsWith("--reasoning="))
    ?.slice("--reasoning=".length);
  const allowedReasoning = new Set(["minimal", "low", "medium", "high"]);
  if (requestedReasoning && !allowedReasoning.has(requestedReasoning)) {
    throw new Error("--reasoning must be minimal, low, medium, or high.");
  }
  const reasoningEffort = (requestedReasoning || "medium") as "minimal" | "low" | "medium" | "high";
  const requestedIds = new Set(args.filter((arg) => !arg.startsWith("--")));
  const activeCases = requestedIds.size
    ? cases.filter((item) => requestedIds.has(item.id))
    : cases;
  if (activeCases.length === 0) throw new Error("No eval case matched the requested ids.");
  const directoryResponse = await fetch(`${API}/api/entities`, { signal: AbortSignal.timeout(15_000) });
  if (!directoryResponse.ok) throw new Error(`Entity directory HTTP ${directoryResponse.status}`);
  const directory = await directoryResponse.json() as { entities: Entity[] };

  const results = [];
  for (const item of activeCases) {
    const started = Date.now();
    try {
      const route = await routeWithModel(
        item.query,
        directory.entities,
        REFERENCE_DATE,
        item.prior || null,
        {
          model: requestedModel,
          reasoningEffort,
        },
      );
      let applied: AppliedSearch | null = null;
      let materializationError: string | null = null;
      try {
        applied = materializeModelRoute(item.query, route, directory.entities, item.prior || null);
      } catch (error) {
        materializationError = error instanceof Error ? error.message : "Unknown materialization error";
      }
      const failures = planFailures(route, applied, item.expected);
      if (materializationError && !item.expected.unresolved) {
        failures.push(materializationError);
      }
      let retrieval = null;
      let retrievalError: string | null = null;
      if (applied) {
        try {
          retrieval = await retrieve(applied);
        } catch (error) {
          retrievalError = error instanceof Error ? error.message : "Unknown retrieval error";
        }
      }
      results.push({
        id: item.id,
        query: item.query,
        pass: failures.length === 0 && retrievalError === null,
        failures,
        materialization_error: materializationError,
        retrieval_error: retrievalError,
        latency_ms: Date.now() - started,
        route,
        applied,
        retrieval,
      });
      process.stdout.write(`${failures.length || retrievalError ? "FAIL" : "PASS"} ${item.id} (${Date.now() - started}ms)\n`);
    } catch (error) {
      results.push({
        id: item.id,
        query: item.query,
        pass: false,
        failures: [error instanceof Error ? error.message : "Unknown eval error"],
        latency_ms: Date.now() - started,
        route: null,
        applied: null,
        retrieval: null,
      });
      process.stdout.write(`FAIL ${item.id} (${Date.now() - started}ms)\n`);
    }
  }

  const passed = results.filter((result) => result.pass).length;
  const generatedAt = new Date().toISOString();
  const report = {
    generated_at: generatedAt,
    reference_date: REFERENCE_DATE,
    planner: "production routeWithModel",
    model: requestedModel || "gemini-3.5-flash-lite",
    reasoning_effort: reasoningEffort,
    retrieval_api: API,
    total: results.length,
    passed,
    failed: results.length - passed,
    results,
  };
  const runId = generatedAt.replace(/[:.]/g, "-");
  const reportDir = resolve(
    process.cwd(),
    "../../artifacts/corpus_browser_router_variety/runs",
    runId,
  );
  await mkdir(reportDir, { recursive: true });
  const outputPath = resolve(reportDir, "eval_report.json");
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  process.stdout.write(`\n${passed}/${results.length} plans passed\n${outputPath}\n`);
  if (passed !== results.length) process.exitCode = 1;
}

void main();
