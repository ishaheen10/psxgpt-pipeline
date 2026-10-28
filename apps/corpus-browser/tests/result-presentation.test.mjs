import assert from "node:assert/strict";
import test from "node:test";

import {
  buildResultDisplaySections,
  sortRecentFirst,
} from "../app/lib/result-presentation.ts";

function source(id, entity, period, facet) {
  return {
    id,
    corpus: "filing",
    entity,
    source_name: entity,
    title: `${entity} page ${id}`,
    period,
    page_number: id,
    summary: "Direct evidence",
    snippet: "Direct evidence",
    bm25_score: 1,
    source_url: null,
    thumbnail_url: null,
    asset_key: null,
    tags: [],
    quality_flags: [],
    retrieval_facets: [facet],
  };
}

function response(results) {
  return {
    query: "compare banks",
    parsed_terms: [],
    match_mode: "any",
    result_count: results.length,
    results,
  };
}

test("sorts relevant result cards newest first", () => {
  const ordered = sortRecentFirst([
    source(1, "UBL", "2022-12-31", "Deposits"),
    source(2, "UBL", "2025-12-31", "Deposits"),
    source(3, "UBL", "2024-12-31", "Deposits"),
  ]);
  assert.deepEqual(ordered.map((item) => item.period), [
    "2025-12-31",
    "2024-12-31",
    "2022-12-31",
  ]);
});

test("clubs multi-ticker results by company with evidence needs inside each company", () => {
  const plan = {
    naturalQuery: "Compare UBL and HBL deposits and capital",
    literalQuery: "deposits and capital",
    lexicalQuery: "deposits",
    fieldQueries: { key: "deposits", summary: "deposits", text: "deposits", heading: "deposits" },
    tickers: ["UBL", "HBL"],
    requiredTickers: ["UBL", "HBL"],
    industries: [],
    years: [],
    dateFrom: null,
    dateTo: null,
    corpora: ["filing"],
    searchFacets: [
      { label: "Deposits", lexicalQuery: "deposits", fieldQueries: {}, corpora: ["filing"] },
      { label: "Capital", lexicalQuery: "capital", fieldQueries: {}, corpora: ["filing"] },
    ],
  };
  const sections = buildResultDisplaySections(response([
    source(1, "HBL", "2024-12-31", "Deposits"),
    source(2, "UBL", "2025-12-31", "Capital"),
    source(3, "UBL", "2024-12-31", "Deposits"),
    source(4, "HBL", "2025-12-31", "Capital"),
  ]), plan);

  assert.deepEqual(sections.map((section) => section.title), ["UBL", "HBL"]);
  assert.deepEqual(sections[0].groups.map((group) => group.label), ["Deposits", "Capital"]);
  assert.deepEqual(sections[1].groups.map((group) => group.label), ["Deposits", "Capital"]);
  assert.equal(sections[0].groups[0].sources[0].entity, "UBL");
});
