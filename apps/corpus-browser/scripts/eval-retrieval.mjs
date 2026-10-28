const baseUrl = (process.argv[2] || "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev").replace(/\/$/, "");

const cases = [
  {
    id: "bank_sector_exposure",
    sourceShape: "exact note plus peer comparison",
    params: {
      q: "credit risk industry sector",
      q_summary: "credit risk industry sector",
      q_text: "credit risk by industry sector",
      q_heading: "credit risk by industry sector",
      tickers: "UBL,HBL,ABL,MCB",
      years: "2023,2024,2025",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "3",
    },
    expectedEntities: ["UBL", "HBL", "ABL", "MCB"],
    expected: /\b(credit risk|industry sector|sector exposure)\b/i,
  },
  {
    id: "bank_capital_ratios",
    sourceShape: "metric comparison from an exact disclosure",
    params: {
      q: "capital adequacy ratios",
      q_summary: "capital adequacy CET1 tier 1 capital ratio leverage ratio",
      q_text: "capital adequacy common equity tier 1 leverage ratio",
      q_heading: "capital adequacy leverage ratio liquidity requirements",
      tickers: "UBL,HBL,ABL,MCB",
      years: "2023,2024,2025",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "3",
    },
    expectedEntities: ["UBL", "HBL", "ABL", "MCB"],
    expected: /\b(capital adequacy|CET ?1|tier 1|leverage ratio)\b/i,
  },
  {
    id: "hmb_deposit_note",
    sourceShape: "known note and line-item lookup",
    params: {
      q: "current deposits customers financial institutions",
      q_summary: "current deposits customers financial institutions",
      q_text: "current deposits customers financial institutions",
      q_heading: "deposits and other accounts",
      tickers: "HMB",
      years: "2024",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "5",
    },
    expectedEntities: ["HMB"],
    expected: /\b(deposits and other accounts|customers|financial institutions)\b/i,
  },
  {
    id: "bank_marketing_expense",
    sourceShape: "same line item across companies and years",
    params: {
      q: "marketing advertisement publicity expense",
      q_summary: "marketing advertisement publicity expense",
      q_text: "marketing advertisement and publicity",
      q_heading: "operating expenses",
      tickers: "UBL,MEBL,FABL",
      years: "2023,2024,2025",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "2",
    },
    expectedEntities: ["UBL", "MEBL", "FABL"],
    expected: /\b(marketing|advertisement|publicity|operating expenses)\b/i,
  },
  {
    id: "luck_capacity_utilization",
    sourceShape: "management narrative and operating KPI",
    params: {
      q: "capacity utilization expansion",
      q_summary: "production capacity utilization expansion",
      q_text: "capacity utilization production expansion",
      q_heading: "capacity utilization",
      tickers: "LUCK",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "5",
    },
    expectedEntities: ["LUCK"],
    expected: /\b(capacity|utilization|production|expansion)\b/i,
  },
  {
    id: "cement_capacity_screen",
    sourceShape: "sector-wide peer discovery with no named ticker",
    params: {
      q: "production capacity cement",
      q_summary: "production capacity cement",
      q_text: "production capacity installed capacity",
      q_heading: "production capacity",
      industries: "cement",
      corpora: "filing",
      filing_cadence: "annual",
      limit: "8",
    },
    expectedEntities: [],
    expected: /\b(cement|clinker|production capacity|capacity and actual production)\b/i,
  },
  {
    id: "mlcf_acquisition_announcement",
    sourceShape: "announcement and corporate event",
    params: {
      q: "acquisition PIOC",
      q_summary: "acquisition Pioneer Cement PIOC",
      q_text: "acquisition Pioneer Cement",
      q_heading: "acquisition Pioneer Cement",
      tickers: "MLCF",
      corpora: "announcement",
      limit: "5",
    },
    expectedEntities: ["MLCF"],
    expected: /\b(acquisition|Pioneer|PIOC)\b/i,
  },
  {
    id: "economic_survey_agriculture",
    sourceShape: "macro report chapter and evidence",
    params: {
      q: "agriculture growth",
      q_summary: "agriculture growth crops livestock",
      q_text: "agriculture growth crops livestock",
      q_heading: "agriculture",
      corpora: "macro",
      limit: "5",
    },
    expectedEntities: [],
    expected: /\b(agriculture|crop|livestock|growth)\b/i,
  },
];

function searchable(result) {
  return [
    result.title,
    result.summary,
    result.snippet,
    ...(result.table_signature || []),
  ].filter(Boolean).join(" ");
}

const output = [];
for (const testCase of cases) {
  const url = new URL("/api/search", baseUrl);
  for (const [key, value] of Object.entries(testCase.params)) url.searchParams.set(key, value);
  const started = performance.now();
  const response = await fetch(url, { headers: { accept: "application/json" } });
  const elapsedMs = Math.round(performance.now() - started);
  const body = await response.json();
  if (!response.ok) {
    output.push({ id: testCase.id, ok: false, status: response.status, error: body.error, elapsed_ms: elapsedMs });
    continue;
  }
  const results = body.results || [];
  const entityCoverage = testCase.expectedEntities.map((entity) => {
    const entityResults = results.filter((result) => result.entity === entity);
    return {
      entity,
      found: entityResults.length > 0,
      relevant: entityResults.some((result) => testCase.expected.test(searchable(result))),
      top_page: entityResults[0]?.page_number ?? null,
      top_title: entityResults[0]?.title ?? null,
    };
  });
  const relevant = results.filter((result) => testCase.expected.test(searchable(result)));
  output.push({
    id: testCase.id,
    shape: testCase.sourceShape,
    ok: testCase.expectedEntities.length
      ? entityCoverage.every((item) => item.found && item.relevant)
      : relevant.length > 0,
    elapsed_ms: elapsedMs,
    result_count: results.length,
    relevant_results: relevant.length,
    phrase_lane_results: results.filter((result) =>
      (result.retrieval_lanes || []).some((lane) => lane.endsWith("_phrase")),
    ).length,
    derived_titles: results.filter((result) => result.derived_heading).length,
    table_signatures: results.filter((result) => (result.table_signature || []).length > 0).length,
    entity_coverage: entityCoverage,
    top_results: results.slice(0, 5).map((result) => ({
      entity: result.entity,
      title: result.title,
      period: result.period,
      page: result.page_number,
    })),
  });
}

const passed = output.filter((result) => result.ok).length;
process.stdout.write(`${JSON.stringify({
  base_url: baseUrl,
  passed,
  total: output.length,
  cases: output,
}, null, 2)}\n`);
process.exitCode = passed === output.length ? 0 : 1;
