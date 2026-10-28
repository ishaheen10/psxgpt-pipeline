const baseUrl = (
  process.argv[2]
  || "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev"
).replace(/\/$/, "");

const cases = [
  {
    id: "hbl_latest_filing_cash",
    cadence: null,
    query: "cash balance bank cash and cash equivalents",
  },
  {
    id: "hbl_latest_quarterly_cash",
    cadence: "quarterly",
    query: "cash balance bank cash and cash equivalents",
  },
  {
    id: "hbl_latest_annual_cash",
    cadence: "annual",
    query: "cash balance bank cash and cash equivalents",
  },
];

async function evaluate(testCase) {
  const url = new URL("/api/search", baseUrl);
  const params = {
    q: "cash balances treasury banks",
    q_summary: "cash balances treasury banks other banks",
    q_text: "cash and balances with treasury banks",
    q_heading: "cash and balances with treasury banks",
    tickers: "HBL",
    corpora: "filing",
    latest: "1",
    limit: "6",
  };
  if (testCase.cadence) params.filing_cadence = testCase.cadence;
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);

  const started = performance.now();
  const response = await fetch(url, {
    headers: { accept: "application/json" },
    signal: AbortSignal.timeout(15_000),
  });
  const body = await response.json();
  const results = body.results || [];
  const expectedPeriod = body.latest_periods?.HBL || null;
  const resultPeriods = [...new Set(results.map((result) => result.period))];
  const exactPeriodOk = Boolean(expectedPeriod)
    && results.length > 0
    && results.every((result) => result.period === expectedPeriod);
  const cadenceOk = !testCase.cadence
    || results.every((result) => result.document_type === testCase.cadence);
  const answerBearingOk = results.slice(0, 3).some((result) =>
    /\bcash\b/i.test(`${result.title} ${result.summary} ${result.snippet}`)
    && /\b(balances?|equivalents?)\b/i.test(`${result.title} ${result.summary} ${result.snippet}`)
  );
  const firstResultExactDisclosureOk = /\bcash and balances with treasury banks\b/i.test(
    `${results[0]?.title || ""} ${results[0]?.summary || ""}`,
  );

  return {
    id: testCase.id,
    ok: response.ok && exactPeriodOk && cadenceOk && answerBearingOk && firstResultExactDisclosureOk,
    status: response.status,
    elapsed_ms: Math.round(performance.now() - started),
    expected_period_from_database: expectedPeriod,
    result_periods: resultPeriods,
    exact_period_ok: exactPeriodOk,
    cadence_ok: cadenceOk,
    answer_bearing_top_3_ok: answerBearingOk,
    first_result_exact_disclosure_ok: firstResultExactDisclosureOk,
    top_results: results.slice(0, 3).map((result) => ({
      title: result.title,
      period: result.period,
      document_type: result.document_type,
      page: result.page_number,
    })),
  };
}

const results = [];
for (const testCase of cases) results.push(await evaluate(testCase));
const report = {
  generated_at: new Date().toISOString(),
  base_url: baseUrl,
  passed: results.filter((result) => result.ok).length,
  total: results.length,
  acceptance: "Every result must equal the database-resolved maximum filing period, match explicit cadence, and rank the exact cash-balance disclosure first.",
  results,
};
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
if (report.passed !== report.total) process.exitCode = 1;
