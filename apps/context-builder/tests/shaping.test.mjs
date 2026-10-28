import assert from "node:assert/strict";
import test from "node:test";

import {
  capPerEntity,
  coverage,
  dedupeAdjacent,
  latestFilingOnly,
  neighborIds,
  sameDocument,
} from "../lib/shaping.ts";
import { adviseQuery } from "../lib/vocabulary.ts";

function page(overrides) {
  return {
    id: 1,
    corpus: "filing",
    entity: "UBL",
    source_name: "UBL",
    title: "page",
    period: "2024-12-31",
    page_number: 10,
    summary: "",
    snippet: "",
    bm25_score: 5,
    source_url: null,
    thumbnail_url: null,
    asset_key: null,
    tags: [],
    quality_flags: [],
    loading: false,
    inclusionMode: "full",
    ...overrides,
  };
}

test("dedupeAdjacent drops a page touching a better-ranked one", () => {
  const kept = dedupeAdjacent([
    page({ id: 1, page_number: 10, bm25_score: 9 }),
    page({ id: 2, page_number: 11, bm25_score: 4 }),
    page({ id: 3, page_number: 40, bm25_score: 3 }),
  ]);
  assert.deepEqual(kept.map((p) => p.page_number).sort((a, b) => a - b), [10, 40]);
});

test("dedupeAdjacent does not merge across filings", () => {
  // Same page number, different period: these are different documents.
  const kept = dedupeAdjacent([
    page({ id: 1, page_number: 10, period: "2024-12-31" }),
    page({ id: 2, page_number: 10, period: "2023-12-31" }),
  ]);
  assert.equal(kept.length, 2);
});

test("capPerEntity keeps the best ranked per company", () => {
  const kept = capPerEntity(
    [
      page({ id: 1, entity: "UBL", bm25_score: 9 }),
      page({ id: 2, entity: "UBL", bm25_score: 8 }),
      page({ id: 3, entity: "UBL", bm25_score: 1 }),
      page({ id: 4, entity: "MCB", bm25_score: 5 }),
    ],
    2,
  );
  assert.equal(kept.length, 3);
  assert.deepEqual(
    kept.filter((p) => p.entity === "UBL").map((p) => p.bm25_score),
    [9, 8],
  );
});

test("latestFilingOnly keeps the newest period per company independently", () => {
  const kept = latestFilingOnly([
    page({ id: 1, entity: "UBL", period: "2024-12-31" }),
    page({ id: 2, entity: "UBL", period: "2022-12-31" }),
    page({ id: 3, entity: "MCB", period: "2023-12-31" }),
  ]);
  assert.deepEqual(kept.map((p) => p.id), [1, 3]);
});

test("neighborIds spans the radius and skips the anchor", () => {
  assert.deepEqual(neighborIds(100, 2), [98, 99, 101, 102]);
});

test("neighborIds never proposes a non-positive id", () => {
  assert.deepEqual(neighborIds(2, 3), [1, 3, 4, 5]);
});

test("sameDocument rejects a neighbour from another filing", () => {
  const origin = { entity: "UBL", period: "2024-12-31" };
  // Running off the end of a document lands on the next filing's first page.
  assert.equal(
    sameDocument(origin, { ticker: "UBL", filing_period: "2023-12-31", filing_type: "annual" }, "annual"),
    false,
  );
  assert.equal(
    sameDocument(origin, { ticker: "MCB", filing_period: "2024-12-31", filing_type: "annual" }, "annual"),
    false,
  );
  // Same ticker and period but the quarterly, not the annual.
  assert.equal(
    sameDocument(origin, { ticker: "UBL", filing_period: "2024-12-31", filing_type: "quarterly" }, "annual"),
    false,
  );
  assert.equal(
    sameDocument(origin, { ticker: "UBL", filing_period: "2024-12-31", filing_type: "annual" }, "annual"),
    true,
  );
});

test("coverage reports pages and periods per company", () => {
  const lines = coverage([
    page({ id: 1, entity: "UBL", period: "2024-12-31" }),
    page({ id: 2, entity: "UBL", period: "2023-12-31" }),
    page({ id: 3, entity: "MCB", period: "2024-12-31" }),
  ]);
  assert.deepEqual(lines[0], { entity: "MCB", periods: ["2024-12-31"], pages: 1 });
  assert.equal(lines[1].entity, "UBL");
  assert.equal(lines[1].pages, 2);
  assert.deepEqual(lines[1].periods, ["2024-12-31", "2023-12-31"]);
});

test("adviseQuery flags the phrase that returns contents pages", () => {
  const advice = adviseQuery("financial statements of UBL");
  assert.ok(advice.weak.some((entry) => entry.term === "financial statements"));
  assert.match(advice.weak[0].matches, /contents/i);
});

test("adviseQuery reports the longest weak phrase, not its fragments", () => {
  // "annual report" and "report" both match; only the specific one is useful.
  const advice = adviseQuery("annual report");
  assert.deepEqual(advice.weak.map((entry) => entry.term), ["annual report"]);
});

test("adviseQuery stays quiet on a high-signal query", () => {
  const advice = adviseQuery("cost of funds and cost to income");
  assert.deepEqual(advice.weak, []);
  assert.equal(advice.noStrongTerm, false);
});

test("adviseQuery offers replacements for a bare weak term", () => {
  const advice = adviseQuery("risk");
  assert.ok(advice.suggestions.includes("key risk"));
});
