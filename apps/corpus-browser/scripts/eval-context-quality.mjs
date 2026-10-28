const apiBase = (
  process.argv.find((arg) => arg.startsWith("--api-base="))?.slice("--api-base=".length)
  || "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev"
).replace(/\/$/, "");
const appBase = (
  process.argv.find((arg) => arg.startsWith("--app-base="))?.slice("--app-base=".length)
  || "https://psxgpt-corpus-browser.isfandiyar-shaheen.workers.dev"
).replace(/\/$/, "");

const cases = [
  {
    id: "bank_sector_exposure",
    query: "Sector level exposure of UBL HBL ABL and MCB",
    expectedEntities: ["UBL", "HBL", "ABL", "MCB"],
    expectedCorpora: ["filing"],
    groups: [/\b(credit risk|lending|advances?)\b/i, /\b(industry|sector|exposure)\b/i],
    requiresNumbers: true,
  },
  {
    id: "bank_capital_ratios",
    query: "Compare CET1 and key capital ratios for UBL HBL ABL and MCB",
    expectedEntities: ["UBL", "HBL", "ABL", "MCB"],
    expectedCorpora: ["filing"],
    groups: [/\b(capital adequacy|common equity tier ?1|CET ?1|tier ?1 capital|leverage ratio)\b/i],
    requiresNumbers: true,
  },
  {
    id: "bank_deposits_funding",
    query: "Compare deposit mix and funding strategy evidence for HBL and UBL",
    expectedEntities: ["HBL", "UBL"],
    expectedCorpora: ["filing"],
    groups: [/\b(deposits?|funding)\b/i, /\b(customers?|financial institutions?|other accounts|remuneration|mix)\b/i],
    requiresNumbers: true,
  },
  {
    id: "bank_marketing_expense",
    query: "Marketing and advertising spend for UBL MEBL and FABL",
    expectedEntities: ["UBL", "MEBL", "FABL"],
    expectedCorpora: ["filing"],
    groups: [/\b(marketing|advertisement|advertising|publicity)\b/i],
    requiresNumbers: true,
  },
  {
    id: "cement_capacity",
    query: "Which cement companies have the most production capacity?",
    expectedEntities: [],
    expectedCorpora: ["filing"],
    groups: [/\b(capacity|actual production|utili[sz]ation)\b/i, /\b(cement|clinker|production)\b/i],
    requiresNumbers: true,
  },
  {
    id: "segment_product_revenue",
    query: "For the past five years show NRL base oil and other segment breakdown",
    expectedEntities: ["NRL"],
    expectedCorpora: ["filing"],
    groups: [/\b(segment|product|disaggregation)\b/i, /\b(revenue|sales|base oils?)\b/i],
    requiresNumbers: true,
  },
  {
    id: "fuel_power_costs",
    query: "For Tariq Glass break down fuel and power costs into electricity and gas",
    expectedEntities: ["TGL"],
    expectedCorpora: ["filing"],
    groups: [/\b(fuel|power|electricity|gas)\b/i, /\b(cost|expense|sales|mix)\b/i],
    requiresNumbers: true,
  },
  {
    id: "pharma_import_fx_risk",
    query: "Compare import FX exposure of AGP and Highnoon",
    expectedEntities: ["AGP", "HINOON"],
    expectedCorpora: ["filing"],
    groups: [/\b(import|foreign exchange|foreign currency|currency risk|raw materials?|pharmaceutical ingredients?)\b/i],
    requiresNumbers: false,
  },
  {
    id: "mlcf_acquisition_announcement",
    query: "Find MLCF announcement about acquiring Pioneer Cement",
    expectedEntities: ["MLCF"],
    expectedCorpora: ["announcement"],
    groups: [/\b(acquisition|acquire|Pioneer|PIOC)\b/i],
    requiresNumbers: false,
  },
  {
    id: "systems_announcements",
    query: "Any new contracts or agreements announced by SYS?",
    expectedEntities: ["SYS"],
    expectedCorpora: ["announcement"],
    groups: [/\b(material|contract|agreement|disclosure|announcement)\b/i],
    requiresNumbers: false,
  },
  {
    id: "macro_agriculture",
    query: "Explain weak major crops versus livestock growth",
    expectedEntities: [],
    expectedCorpora: ["macro"],
    groups: [/\b(agriculture|crops?|livestock)\b/i],
    requiresNumbers: true,
  },
  {
    id: "macro_inflation",
    query: "What happened to food and energy inflation in Pakistan?",
    expectedEntities: [],
    expectedCorpora: ["macro"],
    groups: [/\b(inflation|consumer price|CPI)\b/i],
    requiresNumbers: true,
  },
  {
    id: "macro_manufacturing",
    query: "Which industries drove the LSM contraction?",
    expectedEntities: [],
    expectedCorpora: ["macro"],
    groups: [/(manufacturing|mining|\bLSM\b|large[-_ ]scale)/i],
    requiresNumbers: true,
  },
  {
    id: "company_operating_narrative",
    query: "What is LUCK management saying about expansion and utilisation?",
    expectedEntities: ["LUCK"],
    expectedCorpora: ["filing"],
    groups: [/\b(capacity|utili[sz]ation|expansion|production)\b/i],
    requiresNumbers: false,
  },
];

function sourceKey(source) {
  return `${source.corpus}:${source.id}`;
}

async function jsonFetch(url, init = {}, timeoutMs = 20_000) {
  const response = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
  const body = await response.json();
  if (!response.ok) throw new Error(`${response.status}: ${body.error || "request failed"}`);
  return body;
}

async function routeQuery(query) {
  return jsonFetch(`${appBase}/api/route`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ query, clientYear: new Date().getFullYear(), context: null }),
  });
}

async function searchFacet(route, facet, facetCount) {
  const url = new URL("/api/search", apiBase);
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
    limit: facetCount >= 3 ? "1" : facetCount === 2 ? "4" : "6",
  };
  for (const [key, value] of Object.entries(params)) {
    if (value) url.searchParams.set(key, value);
  }
  const payload = await jsonFetch(url);
  return (payload.results || []).map((source) => ({
    ...source,
    retrieval_facets: [facet.label],
  }));
}

function mergeFacetResults(facets, responses, preferredEntities) {
  const entityOrder = [...new Set([
    ...preferredEntities,
    ...responses.flat().map((source) => source.entity),
  ])];
  const merged = [];
  const seen = new Map();
  for (const entity of entityOrder) {
    const candidates = responses.map((results) => results.filter((source) => source.entity === entity));
    const maxRank = Math.max(0, ...candidates.map((results) => results.length));
    for (let rank = 0; rank < maxRank; rank += 1) {
      for (const results of candidates) {
        const source = results[rank];
        if (!source) continue;
        const key = sourceKey(source);
        const existing = seen.get(key);
        if (existing) {
          existing.retrieval_facets = [...new Set([
            ...(existing.retrieval_facets || []),
            ...(source.retrieval_facets || []),
          ])];
          continue;
        }
        seen.set(key, source);
        merged.push(source);
      }
    }
  }
  return merged;
}

function buildBundle(results, facets, preferredEntities, years) {
  const entities = [...new Set([...preferredEntities, ...results.map((source) => source.entity).filter(Boolean)])];
  const bundle = [];
  const seen = new Set();
  const add = (source) => {
    if (!source || bundle.length >= 16 || seen.has(sourceKey(source))) return;
    seen.add(sourceKey(source));
    bundle.push(source);
  };
  for (const facet of facets) {
    for (const entity of entities) {
      add(results.find((source) =>
        source.entity === entity && (source.retrieval_facets || []).includes(facet.label)
      ));
    }
  }
  if (years.length > 1) {
    const periods = new Set();
    for (const source of [...results].sort((left, right) => right.bm25_score - left.bm25_score)) {
      const periodKey = `${source.entity}:${source.period}`;
      if (periods.has(periodKey)) continue;
      periods.add(periodKey);
      add(source);
    }
  }
  for (const source of results) {
    if (bundle.length >= 3) break;
    add(source);
  }
  return bundle;
}

async function hydrate(source) {
  const detail = await jsonFetch(
    `${apiBase}/api/document?corpus=${encodeURIComponent(source.corpus)}&id=${source.id}`,
  );
  return { ...source, detail };
}

function assess(testCase, source) {
  const content = String(source.detail?.text || "");
  const summary = String(source.summary || source.detail?.search_text || "");
  const titleAndKey = [
    source.title,
    summary,
    source.snippet,
    ...(source.table_signature || []),
  ].filter(Boolean).join(" ");
  const allText = `${titleAndKey}\n${content}`;
  const topicMatch = testCase.groups.every((pattern) => pattern.test(allText));
  const keyMatch = testCase.groups.every((pattern) => pattern.test(titleAndKey));
  const numberTokens = (content.match(/\b\d[\d,]*(?:\.\d+)?%?\b/g) || []).length;
  const tableRows = content.split("\n").filter((line) => /^\s*\|.*\|\s*$/.test(line)).length;
  const sparse = content.trim().length < 250;
  const navigationNoise = (
    /\b(table of contents|contents)\b/i.test(`${source.title}\n${content.slice(0, 1_500)}`)
    && !keyMatch
  ) || /^annual report — page\b/i.test(source.title || "") || sparse;
  const expectedCorpus = testCase.expectedCorpora.includes(source.corpus);
  const answerBearing = topicMatch
    && keyMatch
    && expectedCorpus
    && !navigationNoise
    && (
      !testCase.requiresNumbers
      || numberTokens >= 4
      || tableRows >= 2
    );
  return {
    topic_match: topicMatch,
    key_match: keyMatch,
    expected_corpus: expectedCorpus,
    answer_bearing: answerBearing,
    navigation_noise: navigationNoise,
    content_chars: content.trim().length,
    number_tokens: numberTokens,
    table_rows: tableRows,
  };
}

const output = [];
for (const testCase of cases) {
  const started = performance.now();
  try {
    const route = await routeQuery(testCase.query);
    const facets = route.searchFacets?.length
      ? route.searchFacets
      : [{
          label: "General evidence",
          lexicalQuery: route.lexicalQuery,
          fieldQueries: route.fieldQueries,
        }];
    const responses = await Promise.all(
      facets.map((facet) => searchFacet(route, facet, facets.length)),
    );
    const results = mergeFacetResults(facets, responses, route.tickers || []);
    const firstThree = await Promise.all(results.slice(0, 3).map(hydrate));
    const bundleSources = buildBundle(results, facets, route.tickers || [], route.years || []);
    const hydratedBundle = await Promise.all(bundleSources.map(hydrate));
    const firstThreeAssessment = firstThree.map((source) => ({
      source: {
        corpus: source.corpus,
        id: source.id,
        entity: source.entity,
        period: source.period,
        title: source.title,
        page: source.page_number,
        facets: source.retrieval_facets,
      },
      ...assess(testCase, source),
    }));
    const bundleAssessment = hydratedBundle.map((source) => ({
      source: {
        corpus: source.corpus,
        id: source.id,
        entity: source.entity,
        period: source.period,
        title: source.title,
        page: source.page_number,
        facets: source.retrieval_facets,
      },
      ...assess(testCase, source),
    }));
    const requiredCells = facets.flatMap((facet) =>
      (route.tickers?.length ? route.tickers : [])
        .map((entity) => `${facet.label}:${entity}`)
    );
    const coveredCells = new Set(bundleSources.flatMap((source) =>
      (source.retrieval_facets || []).map((facet) => `${facet}:${source.entity}`)
    ));
    const requestedPeriodCells = (route.years || []).length > 1
      ? (route.tickers || []).flatMap((entity) =>
          route.years.map((year) => `${entity}:${year}`)
        )
      : [];
    const coveredPeriodCells = new Set(bundleSources.map((source) =>
      `${source.entity}:${String(source.period || "").slice(0, 4)}`
    ));
    const routeScopeOk = (route.tickers || []).length === testCase.expectedEntities.length
      && testCase.expectedEntities.every((entity) => route.tickers?.includes(entity))
      && (route.corpora || []).length === testCase.expectedCorpora.length
      && testCase.expectedCorpora.every((corpus) => route.corpora?.includes(corpus));
    const distinctTopEntities = new Set(firstThree.map((source) => source.entity).filter(Boolean)).size;
    const targetDiversity = testCase.expectedEntities.length > 1
      ? Math.min(3, testCase.expectedEntities.length)
      : testCase.expectedEntities.length === 0
        ? Math.min(3, new Set(results.map((source) => source.entity).filter(Boolean)).size)
        : 1;
    const topThreeDiversityOk = distinctTopEntities >= targetDiversity;
    const topThreeExcellent = firstThreeAssessment.length === 3
      && firstThreeAssessment.every((item) => item.answer_bearing)
      && topThreeDiversityOk;
    const bundleCoverageOk = requiredCells.every((cell) => coveredCells.has(cell));
    const bundlePeriodCoverageOk = requestedPeriodCells.every((cell) => coveredPeriodCells.has(cell));
    const bundleExcellent = bundleAssessment.length > 0
      && bundleAssessment.every((item) => item.answer_bearing)
      && bundleCoverageOk
      && bundlePeriodCoverageOk
      && routeScopeOk;
    output.push({
      id: testCase.id,
      query: testCase.query,
      elapsed_ms: Math.round(performance.now() - started),
      routed_by: route.routedBy,
      route_scope_ok: routeScopeOk,
      route: {
        tickers: route.tickers,
        industries: route.industries,
        corpora: route.corpora,
        years: route.years,
        facets: facets.map((facet) => facet.label),
      },
      result_count: results.length,
      top_three_excellent: topThreeExcellent,
      top_three_diversity_ok: topThreeDiversityOk,
      top_three_answer_bearing: firstThreeAssessment.filter((item) => item.answer_bearing).length,
      first_three: firstThreeAssessment,
      bundle_size: bundleAssessment.length,
      bundle_coverage_ok: bundleCoverageOk,
      bundle_period_coverage_ok: bundlePeriodCoverageOk,
      bundle_excellent: bundleExcellent,
      bundle_answer_bearing: bundleAssessment.filter((item) => item.answer_bearing).length,
      bundle_navigation_noise: bundleAssessment.filter((item) => item.navigation_noise).length,
      bundle: bundleAssessment,
    });
  } catch (error) {
    output.push({
      id: testCase.id,
      query: testCase.query,
      error: error instanceof Error ? error.message : String(error),
      top_three_excellent: false,
      bundle_excellent: false,
    });
  }
  process.stderr.write(`[${output.length}/${cases.length}] ${testCase.id}\n`);
}

const valid = output.filter((row) => !row.error);
const report = {
  generated_at: new Date().toISOString(),
  rubric: {
    top_three_excellent: "All first three pages are on-topic, non-navigational, and answer-bearing.",
    answer_bearing: "The full Markdown matches the evidence need and contains substantive text; quantitative asks also require at least four numeric tokens or a Markdown table.",
    bundle_excellent: "Every selected page is answer-bearing and every routed facet x ticker cell is covered.",
  },
  summary: {
    cases: output.length,
    completed: valid.length,
    route_scope_passed: valid.filter((row) => row.route_scope_ok).length,
    top_three_excellent: valid.filter((row) => row.top_three_excellent).length,
    top_three_answer_bearing_pages: valid.reduce((sum, row) => sum + row.top_three_answer_bearing, 0),
    top_three_pages: valid.reduce((sum, row) => sum + row.first_three.length, 0),
    bundles_excellent: valid.filter((row) => row.bundle_excellent).length,
    bundle_answer_bearing_pages: valid.reduce((sum, row) => sum + row.bundle_answer_bearing, 0),
    bundle_pages: valid.reduce((sum, row) => sum + row.bundle_size, 0),
    bundle_navigation_noise_pages: valid.reduce((sum, row) => sum + row.bundle_navigation_noise, 0),
  },
  cases: output,
};

process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
