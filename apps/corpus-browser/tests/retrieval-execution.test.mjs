import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSearchParams,
  executeRetrievalPlan,
  selectContextBundle,
  selectEvidencePreview,
} from "../app/lib/retrieval-execution.ts";

const basePlan = {
  naturalQuery: "Latest UBL cash balance",
  literalQuery: "Latest UBL cash balance",
  lexicalQuery: "cash and bank balances",
  fieldQueries: {
    key: "cash balance cash and bank balances cash equivalents",
    summary: "cash and bank balances cash equivalents",
    text: "cash balance cash equivalents",
    heading: "cash and bank balances",
  },
  tickers: ["UBL"],
  industries: [],
  years: [],
  dateFrom: null,
  dateTo: null,
  corpora: ["filing"],
  filingCadence: "quarterly",
  latest: true,
  routedBy: "router",
};

function result(id, entity = "UBL") {
  return {
    id,
    corpus: "filing",
    entity,
    source_name: entity,
    title: `Page ${id}`,
    period: "2026-03-31",
    page_number: id,
    summary: "Cash and bank balances",
    snippet: "Cash and bank balances",
    bm25_score: 1,
    source_url: null,
    thumbnail_url: null,
    asset_key: null,
    tags: [],
    quality_flags: [],
  };
}

function response(query, results) {
  return {
    query,
    parsed_terms: query.split(" "),
    match_mode: "any",
    result_count: results.length,
    results,
    retrieval_version: "test",
    retrieval_strategy: "field_rrf_v1",
  };
}

test("builds the complete controller-to-search contract without reinterpreting it", () => {
  const params = buildSearchParams(
    basePlan,
    basePlan.lexicalQuery,
    basePlan.fieldQueries,
    6,
    basePlan.corpora,
    true,
  );

  assert.equal(params.get("q"), basePlan.lexicalQuery);
  assert.equal(params.get("q_literal"), basePlan.literalQuery);
  assert.equal(params.get("q_key"), basePlan.fieldQueries.key);
  assert.equal(params.get("q_summary"), basePlan.fieldQueries.summary);
  assert.equal(params.get("q_text"), basePlan.fieldQueries.text);
  assert.equal(params.get("q_heading"), basePlan.fieldQueries.heading);
  assert.equal(params.get("tickers"), "UBL");
  assert.equal(params.get("required_tickers"), "UBL");
  assert.equal(params.get("filing_cadence"), "quarterly");
  assert.equal(params.get("latest"), "1");
  assert.equal(params.has("prefer_recent"), false);
  assert.equal(params.get("debug"), "1");
});

test("keeps recent evidence distinct from a single latest snapshot", () => {
  const params = buildSearchParams(
    {
      ...basePlan,
      temporalMode: "recent",
      latest: false,
      preferRecent: true,
    },
    basePlan.lexicalQuery,
    basePlan.fieldQueries,
    6,
    basePlan.corpora,
  );

  assert.equal(params.get("prefer_recent"), "1");
  assert.equal(params.has("latest"), false);
});

test("prefers recent relevant evidence for an unbounded browse search", () => {
  const params = buildSearchParams(
    {
      ...basePlan,
      temporalMode: "all",
      latest: false,
      preferRecent: undefined,
    },
    basePlan.lexicalQuery,
    basePlan.fieldQueries,
    6,
    basePlan.corpora,
  );

  assert.equal(params.get("prefer_recent"), "1");
  assert.equal(params.has("latest"), false);
});

test("supporting entities remain query evidence rather than widening source scope", () => {
  const params = buildSearchParams(
    {
      ...basePlan,
      tickers: ["MLCF", "PIOC"],
      requiredTickers: ["MLCF"],
      supportingTickers: ["PIOC"],
    },
    "acquisition pioneer cement",
    {
      key: "acquisition pioneer cement",
      summary: "acquisition of pioneer cement",
      text: "acquisition pioneer cement shares",
      heading: "material information acquisition",
    },
    6,
    ["announcement"],
  );

  assert.equal(params.get("tickers"), "MLCF");
  assert.equal(params.get("required_tickers"), "MLCF");
  assert.equal(params.get("q"), "acquisition pioneer cement");
});

test("executes a single routed plan through the same request used by the UI", async () => {
  const requested = [];
  const completed = await executeRetrievalPlan(basePlan, {
    debug: true,
    fetcher: async (input) => {
      requested.push(String(input));
      return Response.json(response(basePlan.lexicalQuery, [result(10)]));
    },
  });

  assert.equal(requested.length, 1);
  const request = new URL(requested[0], "https://ledger.test");
  assert.equal(request.pathname, "/api/search");
  assert.equal(request.searchParams.get("q_key"), basePlan.fieldQueries.key);
  assert.equal(request.searchParams.get("latest"), "1");
  assert.deepEqual(completed.results.map((item) => item.id), [10]);
});

test("executes controller facets independently and merges duplicate evidence", async () => {
  const plan = {
    ...basePlan,
    tickers: ["UBL", "HBL"],
    latest: false,
    filingCadence: "any",
    searchFacets: [
      {
        label: "Cash position",
        evidenceNeed: "cash position evidence",
        lexicalQuery: "cash and bank balances",
        fieldQueries: basePlan.fieldQueries,
        corpora: ["filing"],
      },
      {
        label: "Liquidity",
        evidenceNeed: "liquidity coverage evidence",
        lexicalQuery: "liquidity coverage ratio",
        fieldQueries: {
          key: "liquidity coverage ratio liquid assets",
          summary: "liquidity coverage ratio",
          text: "liquidity liquid assets",
          heading: "liquidity coverage ratio",
        },
        corpora: ["filing"],
      },
    ],
  };
  const requested = [];
  const completed = await executeRetrievalPlan(plan, {
    fetcher: async (input) => {
      const url = new URL(String(input), "https://ledger.test");
      requested.push(url);
      const isCash = url.searchParams.get("q") === "cash and bank balances";
      return Response.json(response(
        url.searchParams.get("q"),
        isCash ? [result(1), result(2, "HBL")] : [result(1), result(3, "HBL")],
      ));
    },
  });

  assert.equal(requested.length, 2);
  assert.ok(requested.every((request) => request.searchParams.get("limit") === "4"));
  assert.deepEqual(
    requested.map((request) => request.searchParams.get("q_literal")),
    ["cash position evidence", "liquidity coverage evidence"],
  );
  assert.deepEqual(completed.results.map((item) => item.id), [1, 1, 2, 3].filter(
    (id, index, values) => values.indexOf(id) === index,
  ));
  assert.deepEqual(
    completed.results.find((item) => item.id === 1)?.retrieval_facets,
    ["Cash position", "Liquidity"],
  );
  assert.equal(completed.match_mode, "any");
});

test("selects the same evidence grid the UI compiles for multi-entity research", () => {
  const plan = {
    ...basePlan,
    tickers: ["UBL", "HBL"],
    searchFacets: [
      { label: "Cash position", lexicalQuery: "cash", fieldQueries: basePlan.fieldQueries },
      { label: "Liquidity", lexicalQuery: "liquidity", fieldQueries: basePlan.fieldQueries },
    ],
  };
  const results = [
    { ...result(1, "UBL"), retrieval_facets: ["Cash position"] },
    { ...result(2, "UBL"), retrieval_facets: ["Liquidity"] },
    { ...result(3, "HBL"), retrieval_facets: ["Cash position"] },
    { ...result(4, "HBL"), retrieval_facets: ["Liquidity"] },
    { ...result(5, "UBL"), retrieval_facets: ["Cash position"] },
  ];
  const search = response("bank evidence", results);

  assert.deepEqual(
    selectEvidencePreview(search, plan).map(({ facet, source }) => `${facet}:${source.entity}:${source.id}`),
    [
      "Cash position:UBL:1",
      "Cash position:HBL:3",
      "Liquidity:UBL:2",
      "Liquidity:HBL:4",
    ],
  );
  assert.deepEqual(
    selectContextBundle(search, plan).map((item) => item.id),
    [1, 3, 2, 4],
  );
});
