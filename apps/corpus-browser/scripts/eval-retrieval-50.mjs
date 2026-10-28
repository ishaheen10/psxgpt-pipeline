const args = process.argv.slice(2);
const positionalBaseUrl = args.find((arg) => !arg.startsWith("--"));
const baseUrl = (
  positionalBaseUrl
  || "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev"
).replace(/\/$/, "");
const routerBaseUrl = args
  .find((arg) => arg.startsWith("--router-base="))
  ?.slice("--router-base=".length)
  .replace(/\/$/, "") || null;
const summaryOnly = process.argv.includes("--summary");
const metricsOnly = process.argv.includes("--metrics-only");
const routedOnly = process.argv.includes("--routed-only");
const endToEndOnly = process.argv.includes("--e2e-only");

function fetchWithTimeout(input, init = {}, timeoutMs = 15_000) {
  return fetch(input, { ...init, signal: AbortSignal.timeout(timeoutMs) });
}

function family(config) {
  return config.variants.map((variant, index) => {
    const variantConfig = typeof variant === "string" ? { query: variant } : variant;
    return {
    id: `${config.id}_${index + 1}`,
    family: config.id,
    shape: config.shape,
    naturalQuery: variantConfig.query,
    params: { ...config.params, ...(variantConfig.params || {}) },
    routed: { ...config.routed, ...(variantConfig.routed || {}) },
    expectedEntities: variantConfig.expectedEntities || config.expectedEntities || [],
    relevantGroups: config.relevantGroups,
    minRelevant: variantConfig.minRelevant || config.minRelevant || 1,
    };
  });
}

const cases = [
  ...family({
    id: "bank_sector_exposure",
    shape: "exact disclosure and peer comparison",
    variants: [
      {
        query: "Show me sector level exposure for UBL",
        params: { tickers: "UBL" },
        expectedEntities: ["UBL"],
      },
      "Get sector level lending exposure for UBL HBL ABL and MCB",
      {
        query: "Compare sector exposure of UBL and HBL",
        params: { tickers: "UBL,HBL" },
        expectedEntities: ["UBL", "HBL"],
      },
      {
        query: "Which industries are UBL HBL and MCB lending to?",
        params: { tickers: "UBL,HBL,MCB" },
        expectedEntities: ["UBL", "HBL", "MCB"],
      },
      {
        query: "sector wise advances for ABL vs MCB latest annual report",
        params: { tickers: "ABL,MCB" },
        expectedEntities: ["ABL", "MCB"],
      },
    ],
    params: {
      tickers: "UBL,HBL,ABL,MCB",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "3",
    },
    routed: {
      q: "credit risk industry sector",
      q_summary: "credit risk lending exposure industry sector",
      q_text: "credit risk by industry sector gross advances",
      q_heading: "credit risk by industry sector",
    },
    expectedEntities: ["UBL", "HBL", "ABL", "MCB"],
    relevantGroups: [/\b(credit risk|lending|advances?)\b/i, /\b(industry|sector|exposure)\b/i],
  }),
  ...family({
    id: "bank_capital_ratios",
    shape: "metric comparison from exact disclosures",
    variants: [
      "Include key capital ratios for UBL HBL ABL and MCB",
      {
        query: "Compare CET1 and capital adequacy of HBL and UBL",
        params: { tickers: "HBL,UBL" },
        expectedEntities: ["HBL", "UBL"],
      },
      "latest tier 1 capital ratios for the big four banks",
      "How well capitalised are ABL MCB HBL and UBL?",
    ],
    params: {
      tickers: "UBL,HBL,ABL,MCB",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "3",
    },
    routed: {
      q: "capital adequacy ratios",
      q_summary: "capital adequacy CET1 tier 1 capital ratio leverage ratio",
      q_text: "capital adequacy common equity tier 1 leverage ratio",
      q_heading: "capital adequacy leverage ratio liquidity requirements",
    },
    expectedEntities: ["UBL", "HBL", "ABL", "MCB"],
    relevantGroups: [/\b(capital adequacy|common equity tier ?1|CET ?1|tier ?1 capital|leverage ratio)\b/i],
  }),
  ...family({
    id: "bank_deposits_funding",
    shape: "exact note and funding evidence",
    variants: [
      {
        query: "current deposits from customers and financial institutions for HMB",
        params: { tickers: "HMB" },
        expectedEntities: ["HMB"],
      },
      {
        query: "Find the deposits and other accounts note for HMB 2024",
        params: { tickers: "HMB" },
        expectedEntities: ["HMB"],
      },
      {
        query: "Compare deposit mix and funding strategy evidence for HBL and UBL",
        params: { tickers: "HBL,UBL" },
        expectedEntities: ["HBL", "UBL"],
      },
      {
        query: "Where are UBL deposits coming from and what do they cost?",
        params: { tickers: "UBL" },
        expectedEntities: ["UBL"],
      },
    ],
    params: {
      tickers: "HMB,HBL,UBL",
      years: "2024",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "4",
    },
    routed: {
      q: "deposits funding customers financial institutions",
      q_summary: "deposit mix customers financial institutions funding",
      q_text: "current deposits customers financial institutions remuneration",
      q_heading: "deposits and other accounts",
    },
    expectedEntities: ["HMB", "HBL", "UBL"],
    relevantGroups: [/\b(deposits?|funding)\b/i, /\b(customers?|financial institutions?|other accounts|remuneration|mix)\b/i],
  }),
  ...family({
    id: "bank_marketing_expense",
    shape: "same line item across companies",
    variants: [
      "marketing and advertising spend for UBL MEBL and FABL",
      "Compare publicity expense of Meezan UBL and Faysal Bank",
      "find marketing advertisement publicity line item for these banks",
    ],
    params: {
      tickers: "UBL,MEBL,FABL",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "3",
    },
    routed: {
      q: "marketing advertisement publicity expense",
      q_summary: "marketing advertisement publicity expense",
      q_text: "marketing advertisement and publicity",
      q_heading: "operating expenses",
    },
    expectedEntities: ["UBL", "MEBL", "FABL"],
    relevantGroups: [/\b(marketing|advertisement|advertising|publicity)\b/i],
  }),
  ...family({
    id: "cement_capacity",
    shape: "sector screen and operating KPI",
    variants: [
      "Which cement companies have the most production capacity?",
      "Compare installed cement capacity across the sector",
      "capacity and actual production for LUCK CHCC FCCL and MLCF",
      "Show capacity utilisation of Pakistani cement makers",
      "How does the new capacity fit into current capacity utilization numbers?",
    ],
    params: {
      industries: "cement",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "8",
    },
    routed: {
      q: "production capacity cement",
      q_summary: "cement production capacity utilization clinker",
      q_text: "production capacity installed capacity actual production",
      q_heading: "production capacity",
    },
    relevantGroups: [/\b(capacity|actual production|utili[sz]ation)\b/i, /\b(cement|clinker|production)\b/i],
    minRelevant: 3,
  }),
  ...family({
    id: "segment_product_revenue",
    shape: "segment or product breakdown",
    variants: [
      {
        query: "For the past five years show NRL base oil and other segment breakdown",
        params: { tickers: "NRL" },
        expectedEntities: ["NRL"],
      },
      {
        query: "compare NRL base oils segment revenue with its other business",
        params: { tickers: "NRL" },
        expectedEntities: ["NRL"],
      },
      {
        query: "Do we have a sales by product breakdown for NATF?",
        params: { tickers: "NATF" },
        expectedEntities: ["NATF"],
      },
      {
        query: "Find disaggregation of revenue for National Foods",
        params: { tickers: "NATF" },
        expectedEntities: ["NATF"],
      },
    ],
    params: {
      tickers: "NRL,NATF",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "5",
    },
    routed: {
      q: "segment product revenue breakdown",
      q_summary: "segment revenue product sales disaggregation",
      q_text: "base oils segment revenue sales by product disaggregation of revenue",
      q_heading: "segment information disaggregation of revenue",
    },
    expectedEntities: ["NRL", "NATF"],
    relevantGroups: [/\b(segment|product|disaggregation)\b/i, /\b(revenue|sales|base oils?)\b/i],
  }),
  ...family({
    id: "fuel_power_costs",
    shape: "cost line-item breakdown",
    variants: [
      "For Tariq Glass break down fuel and power costs into electricity and gas",
      "TGL electricity versus gas expense over the last five years",
      "what is the fuel and power mix in Tariq Glass cost of sales?",
    ],
    params: {
      tickers: "TGL",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "5",
    },
    routed: {
      q: "fuel power costs",
      q_summary: "fuel power",
      q_text: "fuel and power",
      q_heading: "fuel and power",
    },
    expectedEntities: ["TGL"],
    relevantGroups: [/\b(fuel|power|electricity|gas)\b/i, /\b(cost|expense|sales|mix)\b/i],
  }),
  ...family({
    id: "pharma_import_fx_risk",
    shape: "filing narrative and risk evidence",
    variants: [
      {
        query: "Has AGP reduced dependence on imported APIs or foreign exchange?",
        params: { tickers: "AGP" },
        expectedEntities: ["AGP"],
      },
      "Compare import FX exposure of AGP and Highnoon",
      {
        query: "What do the notes say about imported raw materials and currency risk for pharma?",
        params: { tickers: "", industries: "pharma" },
        expectedEntities: [],
        minRelevant: 2,
      },
    ],
    params: {
      tickers: "AGP,HINOON",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "5",
    },
    routed: {
      q: "foreign currency risk imports",
      q_summary: "foreign currency risk imports",
      q_text: "foreign currency exposure imported raw materials",
      q_heading: "currency risk",
    },
    expectedEntities: ["AGP", "HINOON"],
    relevantGroups: [/\b(import|foreign exchange|foreign currency|currency risk|raw materials?|pharmaceutical ingredients?)\b/i],
  }),
  ...family({
    id: "mlcf_acquisition_announcement",
    shape: "corporate announcement event",
    variants: [
      "Find MLCF announcement about acquiring Pioneer Cement",
      "What did Maple Leaf disclose about PIOC acquisition?",
      "latest material information on MLCF buying Pioneer",
      "MLCF acquisition notice and follow up disclosures",
    ],
    params: {
      tickers: "MLCF",
      corpora: "announcement",
      limit: "5",
    },
    routed: {
      q: "acquisition PIOC",
      q_summary: "acquisition Pioneer Cement PIOC",
      q_text: "acquisition Pioneer Cement",
      q_heading: "acquisition Pioneer Cement",
    },
    expectedEntities: ["MLCF"],
    relevantGroups: [/\b(acquisition|acquire|Pioneer|PIOC)\b/i],
  }),
  ...family({
    id: "systems_announcements",
    shape: "announcement review",
    variants: [
      "Review recent Systems Limited material information",
      "Any new contracts or agreements announced by SYS?",
      "Separate recent SYS announcements into positive and negative",
    ],
    params: {
      tickers: "SYS",
      corpora: "announcement",
      limit: "5",
    },
    routed: {
      q: "material information contract agreement",
      q_summary: "material information contract agreement business",
      q_text: "material information contract agreement",
      q_heading: "material information",
    },
    expectedEntities: ["SYS"],
    relevantGroups: [/\b(material|contract|agreement|disclosure|announcement)\b/i],
  }),
  ...family({
    id: "macro_agriculture",
    shape: "Economic Survey chapter evidence",
    variants: [
      "What does the Economic Survey say about agriculture growth?",
      "crop and livestock performance in Pakistan latest survey",
      "Explain weak major crops versus livestock growth",
    ],
    params: {
      corpora: "macro",
      limit: "5",
    },
    routed: {
      q: "agriculture growth",
      q_summary: "agriculture growth crops livestock",
      q_text: "agriculture growth major crops livestock",
      q_heading: "agriculture",
    },
    relevantGroups: [/\b(agriculture|crops?|livestock)\b/i],
    minRelevant: 3,
  }),
  ...family({
    id: "macro_inflation",
    shape: "macro trend and drivers",
    variants: [
      "latest Economic Survey inflation drivers",
      "What happened to food and energy inflation in Pakistan?",
      "CPI trend and inflation outlook from authoritative sources",
    ],
    params: {
      corpora: "macro",
      limit: "5",
    },
    routed: {
      q: "inflation consumer price index",
      q_summary: "inflation CPI food energy prices",
      q_text: "consumer price index food inflation energy inflation",
      q_heading: "inflation",
    },
    relevantGroups: [/\b(inflation|consumer price|CPI)\b/i],
    minRelevant: 3,
  }),
  ...family({
    id: "macro_manufacturing",
    shape: "macro sector research",
    variants: [
      "How is large scale manufacturing doing in Pakistan?",
      "Economic Survey evidence on manufacturing and mining growth",
      "Which industries drove the LSM contraction?",
    ],
    params: {
      corpora: "macro",
      limit: "5",
    },
    routed: {
      q: "large scale manufacturing growth",
      q_summary: "large scale manufacturing mining industries growth contraction",
      q_text: "large scale manufacturing growth industries contraction",
      q_heading: "manufacturing and mining",
    },
    relevantGroups: [/(manufacturing|mining|\bLSM\b|large[-_ ]scale)/i],
    minRelevant: 3,
  }),
  ...family({
    id: "company_operating_narrative",
    shape: "company narrative and operating evidence",
    variants: [
      {
        query: "Give me Cherat Cement products production capacity and capacity utilization",
        params: { tickers: "CHCC" },
        expectedEntities: ["CHCC"],
      },
      {
        query: "Analyse NATF export progress from its annual reports",
        params: { tickers: "NATF" },
        expectedEntities: ["NATF"],
      },
      {
        query: "What is LUCK management saying about expansion and utilisation?",
        params: { tickers: "LUCK" },
        expectedEntities: ["LUCK"],
      },
    ],
    params: {
      tickers: "CHCC,NATF,LUCK",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "5",
    },
    routed: {
      q: "production capacity utilization exports expansion",
      q_summary: "products production capacity utilization exports expansion",
      q_text: "production capacity capacity utilization export sales expansion",
      q_heading: "production capacity directors report",
    },
    expectedEntities: ["CHCC", "NATF", "LUCK"],
    relevantGroups: [/\b(capacity|utili[sz]ation|exports?|expansion|production)\b/i],
  }),
];

if (cases.length !== 50) {
  throw new Error(`Expected exactly 50 evaluation cases, found ${cases.length}.`);
}

if (args.includes("--export-cases")) {
  process.stdout.write(`${JSON.stringify(cases.map((testCase) => ({
    id: testCase.id,
    family: testCase.family,
    shape: testCase.shape,
    natural_query: testCase.naturalQuery,
    params: testCase.params,
    routed: testCase.routed,
    expected_entities: testCase.expectedEntities,
    min_relevant: testCase.minRelevant,
  })))}\n`);
  process.exit(0);
}

function searchable(result) {
  return [
    result.title,
    result.summary,
    result.snippet,
    ...(result.table_signature || []),
  ].filter(Boolean).join(" ");
}

function isRelevant(testCase, result) {
  const text = searchable(result);
  return testCase.relevantGroups.every((pattern) => pattern.test(text));
}

function scopeParams(testCase, mode) {
  const params = { ...testCase.params };
  if (mode === "raw") {
    params.q = testCase.naturalQuery;
  } else {
    Object.assign(params, testCase.routed);
  }
  return params;
}

function evaluateResults(testCase, results, elapsedMs, extra = {}) {
  const relevantIndexes = results
    .map((result, index) => isRelevant(testCase, result) ? index : -1)
    .filter((index) => index >= 0);
  const entityCoverage = testCase.expectedEntities.map((entity) => {
    const entityResults = results.filter((result) => result.entity === entity);
    const relevantIndex = results.findIndex(
      (result) => result.entity === entity && isRelevant(testCase, result),
    );
    return {
      entity,
      found: entityResults.length > 0,
      relevant: relevantIndex >= 0,
      first_relevant_rank: relevantIndex >= 0 ? relevantIndex + 1 : null,
      top_title: entityResults[0]?.title || null,
    };
  });
  const enoughRelevant = relevantIndexes.length >= testCase.minRelevant;
  const entitiesRelevant = testCase.expectedEntities.length
    ? entityCoverage.every((item) => item.found && item.relevant)
    : true;
  const topFive = results.slice(0, 5);
  const relevantTopFive = topFive.filter((result) => isRelevant(testCase, result)).length;

  return {
    ok: enoughRelevant && entitiesRelevant,
    elapsed_ms: elapsedMs,
    result_count: results.length,
    relevant_results: relevantIndexes.length,
    first_relevant_rank: relevantIndexes.length ? relevantIndexes[0] + 1 : null,
    precision_at_5: topFive.length ? relevantTopFive / topFive.length : 0,
    generic_title_rate: results.length
      ? results.filter((result) => /^annual report — page\b/i.test(result.title || "")).length / results.length
      : 0,
    entity_coverage: entityCoverage,
    top_results: results.slice(0, 5).map((result) => ({
      entity: result.entity,
      title: result.title,
      period: result.period,
      page: result.page_number,
      relevant: isRelevant(testCase, result),
    })),
    ...extra,
  };
}

async function runMode(testCase, mode) {
  const url = new URL("/api/search", baseUrl);
  for (const [key, value] of Object.entries(scopeParams(testCase, mode))) {
    url.searchParams.set(key, value);
  }
  const started = performance.now();
  let response;
  try {
    response = await fetchWithTimeout(url, { headers: { accept: "application/json" } });
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Search timed out",
      elapsed_ms: Math.round(performance.now() - started),
      result_count: 0,
    };
  }
  const elapsedMs = Math.round(performance.now() - started);
  const body = await response.json();
  if (!response.ok) {
    return {
      ok: false,
      status: response.status,
      error: body.error || "Search failed",
      elapsed_ms: elapsedMs,
      result_count: 0,
    };
  }

  const results = body.results || [];
  return evaluateResults(testCase, results, elapsedMs);
}

async function runEndToEnd(testCase) {
  const started = performance.now();
  let routeResponse;
  try {
    routeResponse = await fetchWithTimeout(`${routerBaseUrl}/api/route`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ query: testCase.naturalQuery, clientYear: new Date().getFullYear(), context: null }),
    });
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Router timed out",
      elapsed_ms: Math.round(performance.now() - started),
      result_count: 0,
      route_scope_ok: false,
    };
  }
  const route = await routeResponse.json();
  if (!routeResponse.ok) {
    return {
      ok: false,
      status: routeResponse.status,
      error: route.error || "Router failed",
      elapsed_ms: Math.round(performance.now() - started),
      result_count: 0,
      route_scope_ok: false,
    };
  }

  const expectedCorpora = String(testCase.params.corpora || "").split(",").filter(Boolean);
  const routeScopeOk = testCase.expectedEntities.every((entity) => route.tickers?.includes(entity))
    && expectedCorpora.every((corpus) => route.corpora?.includes(corpus));
  const facets = route.searchFacets?.length
    ? route.searchFacets
    : [{
        label: "General evidence",
        lexicalQuery: route.lexicalQuery,
        fieldQueries: route.fieldQueries,
      }];
  const merged = [];
  const seen = new Set();

  const facetResults = await Promise.all(facets.map(async (facet) => {
    const searchUrl = new URL("/api/search", baseUrl);
    const params = {
      q: facet.lexicalQuery || route.lexicalQuery,
      q_summary: facet.fieldQueries?.summary || route.fieldQueries?.summary,
      q_text: facet.fieldQueries?.text || route.fieldQueries?.text,
      q_heading: facet.fieldQueries?.heading || route.fieldQueries?.heading,
      tickers: (route.tickers || []).join(","),
      industries: (route.industries || []).join(","),
      corpora: (route.corpora || []).join(","),
      years: (route.years || []).join(","),
      date_from: route.dateFrom || "",
      date_to: route.dateTo || "",
      filing_cadence: route.filingCadence === "any" ? "" : route.filingCadence,
      limit: facets.length >= 3 ? "1" : facets.length === 2 ? "4" : "6",
    };
    for (const [key, value] of Object.entries(params)) {
      if (value) searchUrl.searchParams.set(key, value);
    }
    let response;
    try {
      response = await fetchWithTimeout(searchUrl, { headers: { accept: "application/json" } });
    } catch {
      return [];
    }
    const body = await response.json();
    return response.ok ? body.results || [] : [];
  }));
  for (const results of facetResults) {
    for (const result of results) {
      const key = `${result.corpus}:${result.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(result);
    }
  }

  return evaluateResults(testCase, merged, Math.round(performance.now() - started), {
    route_scope_ok: routeScopeOk,
    routed_by: route.routedBy,
    route: {
      tickers: route.tickers,
      industries: route.industries,
      corpora: route.corpora,
      years: route.years,
      facets: facets.map((facet) => facet.label),
    },
  });
}

const output = [];
// Each search fans out into several D1 FTS lanes. Keep the benchmark
// sequential so Worker/D1 concurrency limits are not mistaken for bad ranking.
const concurrency = 1;
for (let index = 0; index < cases.length; index += concurrency) {
  const batch = cases.slice(index, index + concurrency);
  const batchResults = await Promise.all(batch.map(async (testCase) => ({
    id: testCase.id,
    family: testCase.family,
    shape: testCase.shape,
    natural_query: testCase.naturalQuery,
    raw: routedOnly || endToEndOnly ? null : await runMode(testCase, "raw"),
    routed: endToEndOnly ? null : await runMode(testCase, "routed"),
    end_to_end: routerBaseUrl ? await runEndToEnd(testCase) : null,
  })));
  output.push(...batchResults);
  process.stderr.write(`[${Math.min(index + batch.length, cases.length)}/${cases.length}] routed evaluation complete\n`);
}

function aggregate(mode) {
  const rows = output.map((row) => row[mode]).filter(Boolean);
  if (!rows.length) return null;
  const latency = rows.map((row) => row.elapsed_ms).sort((left, right) => left - right);
  return {
    passed: rows.filter((row) => row.ok).length,
    total: rows.length,
    pass_rate: rows.filter((row) => row.ok).length / rows.length,
    mean_precision_at_5: rows.reduce((sum, row) => sum + (row.precision_at_5 || 0), 0) / rows.length,
    mean_generic_title_rate: rows.reduce((sum, row) => sum + (row.generic_title_rate || 0), 0) / rows.length,
    median_latency_ms: latency[Math.floor(latency.length / 2)] || 0,
    p90_latency_ms: latency[Math.floor(latency.length * 0.9)] || 0,
  };
}

const families = [...new Set(output.map((row) => row.family))].map((familyId) => {
  const rows = output.filter((row) => row.family === familyId);
  return {
    family: familyId,
    cases: rows.length,
    raw_passed: routedOnly || endToEndOnly ? null : rows.filter((row) => row.raw?.ok).length,
    routed_passed: endToEndOnly ? null : rows.filter((row) => row.routed?.ok).length,
    end_to_end_passed: routerBaseUrl ? rows.filter((row) => row.end_to_end?.ok).length : null,
    route_scope_passed: routerBaseUrl ? rows.filter((row) => row.end_to_end?.route_scope_ok).length : null,
    routed_mean_precision_at_5:
      endToEndOnly
        ? null
        : rows.reduce((sum, row) => sum + (row.routed?.precision_at_5 || 0), 0) / rows.length,
  };
});

const routedFailures = output
  .filter((row) => row.routed && !row.routed.ok)
  .map((row) => ({
    id: row.id,
    family: row.family,
    natural_query: row.natural_query,
    status: row.routed.status || null,
    error: row.routed.error || null,
    result_count: row.routed.result_count,
    entity_coverage: row.routed.entity_coverage,
    top_results: row.routed.top_results,
  }));
const endToEndFailures = routerBaseUrl
  ? output.filter((row) => !row.end_to_end?.ok).map((row) => ({
      id: row.id,
      family: row.family,
      natural_query: row.natural_query,
      status: row.end_to_end?.status || null,
      error: row.end_to_end?.error || null,
      route_scope_ok: row.end_to_end?.route_scope_ok,
      route: row.end_to_end?.route,
      entity_coverage: row.end_to_end?.entity_coverage,
      top_results: row.end_to_end?.top_results,
    }))
  : [];

const report = {
  generated_at: new Date().toISOString(),
  base_url: baseUrl,
  design: {
    source: "50 paraphrases modeled on the 4,134 production D1 query log rows",
    comparison: "raw natural-language BM25 versus gold-scoped routed Q/K field queries",
    note: routerBaseUrl
      ? "Gold scope isolates ranker quality; end_to_end scores the deployed router plus live ranker."
      : "Gold scope isolates retrieval quality; pass --router-base to score the deployed router too.",
  },
  raw: aggregate("raw"),
  routed: aggregate("routed"),
  end_to_end: aggregate("end_to_end"),
  families,
  routed_failures: routedFailures,
  end_to_end_failures: endToEndFailures,
  ...(!summaryOnly ? { cases: output } : {}),
};
const printable = metricsOnly
  ? {
      generated_at: report.generated_at,
      base_url: report.base_url,
      raw: report.raw,
      routed: report.routed,
      end_to_end: report.end_to_end,
      families: report.families,
      routed_failure_count: routedFailures.length,
      routed_failure_sample: routedFailures.slice(0, 5),
      end_to_end_failure_count: endToEndFailures.length,
      end_to_end_failure_sample: endToEndFailures.slice(0, 5),
    }
  : report;
process.stdout.write(`${JSON.stringify(printable, null, 2)}\n`);

process.exitCode = routedFailures.length || endToEndFailures.length ? 1 : 0;
