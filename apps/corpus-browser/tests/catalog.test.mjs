import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCatalogFacets,
  canonicalIndustry,
  industryMatches,
} from "../api/catalog.ts";

test("normalizes pipeline industry values into a stable analyst taxonomy", () => {
  assert.deepEqual(canonicalIndustry("COMMERCIAL BANKS"), {
    id: "banking",
    name: "Banking",
    parentId: "financials",
    parentName: "Financials",
    path: "Financials / Banking",
  });
  assert.deepEqual(canonicalIndustry("CEMENT"), {
    id: "cement",
    name: "Cement",
    parentId: "materials",
    parentName: "Materials",
    path: "Materials / Cement",
  });
  assert.equal(industryMatches("COMMERCIAL BANKS", ["banking"]), true);
  assert.equal(industryMatches("Commercial Banks", ["cement"]), false);
});

test("builds contextual facets from only the current result set", () => {
  const facets = buildCatalogFacets(
    [
      {
        corpus: "filing",
        entity: "UBL",
        industry_id: "banking",
        industry: "Banking",
        period: "2023-12-31",
        publisher: "UBL",
      },
      {
        corpus: "filing",
        entity: "HBL",
        industry_id: "banking",
        industry: "Banking",
        period: "2023-12-31",
        publisher: "HBL",
      },
      {
        corpus: "announcement",
        entity: "UBL",
        industry_id: "banking",
        industry: "Banking",
        period: "2024-01-10",
        publisher: "PSX",
      },
    ],
    {
      tickers: ["UBL"],
      industryTerms: [],
      years: [],
      corpora: ["filing"],
    },
  );

  const entityFacet = facets.find((item) => item.key === "entity");
  const industryFacet = facets.find((item) => item.key === "industry");
  const corpusFacet = facets.find((item) => item.key === "corpus");

  assert.deepEqual(
    entityFacet?.values.map(({ value, count, selected }) => ({ value, count, selected })),
    [
      { value: "UBL", count: 2, selected: true },
      { value: "HBL", count: 1, selected: false },
    ],
  );
  assert.equal(industryFacet, undefined);
  assert.deepEqual(
    corpusFacet?.values.map(({ value, count, selected }) => ({ value, count, selected })),
    [
      { value: "filing", count: 2, selected: true },
      { value: "announcement", count: 1, selected: false },
    ],
  );
});
