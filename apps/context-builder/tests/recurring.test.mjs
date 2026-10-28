import assert from "node:assert/strict";
import test from "node:test";

import {
  RECURRENCE_THRESHOLD,
  collapseRecurring,
  familyMembers,
  summarySimilarity,
} from "../lib/recurring.ts";

function result(overrides) {
  return {
    id: 1,
    corpus: "filing",
    entity: "MCB",
    source_name: "MCB",
    title: "Annual report",
    period: "2024-12-31",
    page_number: 132,
    summary: "",
    snippet: "",
    bm25_score: 10,
    source_url: null,
    thumbnail_url: null,
    asset_key: null,
    tags: [],
    quality_flags: [],
    ...overrides,
  };
}

// The exact summaries observed in a real search, restated across four filings.
const BOILERPLATE_2023 =
  "share price sensitivity analysis discount rate monetary policy inflation minimum deposit rate regulatory risk political stability law order share price 172.55 pkr";
const BOILERPLATE_2021 =
  "share price sensitivity analysis discount rate monetary policy minimum deposit rate regulatory risk inflation political stability law order";
const BOILERPLATE_2024 =
  "share price sensitivity analysis market capitalization 333357 million pkr share price 281.30 pkr inflation monetary policy minimum deposit rate regulatory risk";
const DEPOSIT_PAGE =
  "deposit mobilization net interest margin nim non markup income double digit growth expenses growth cet1 ratio dividend payout";

test("recognises a disclosure restated in a later year", () => {
  assert.ok(summarySimilarity(BOILERPLATE_2023, BOILERPLATE_2021) >= RECURRENCE_THRESHOLD);
});

test("does not conflate genuinely different disclosures", () => {
  assert.ok(summarySimilarity(BOILERPLATE_2023, DEPOSIT_PAGE) < 0.2);
});

test("figures changing year to year do not split a family", () => {
  // 2024 swaps "political stability law order" for a market capitalisation.
  // Jaccard scores this pair 0.62 and would wrongly split it; overlap sees 0.81.
  assert.ok(summarySimilarity(BOILERPLATE_2023, BOILERPLATE_2024) >= RECURRENCE_THRESHOLD);
});

test("four years of boilerplate collapse into one row", () => {
  const families = collapseRecurring([
    result({ id: 1, period: "2023-12-31", summary: BOILERPLATE_2023, bm25_score: 15.73 }),
    result({ id: 2, period: "2021-12-31", summary: BOILERPLATE_2021, bm25_score: 15.33 }),
    result({ id: 3, period: "2022-12-31", summary: BOILERPLATE_2021, bm25_score: 15.33 }),
    result({ id: 4, period: "2024-12-31", summary: BOILERPLATE_2024, bm25_score: 15.28 }),
    result({ id: 5, period: "2023-12-31", summary: DEPOSIT_PAGE, bm25_score: 13.91 }),
  ]);

  // Was five rows, four of them the same page. Now two distinct disclosures.
  assert.equal(families.length, 2);
  assert.equal(families[0].variants.length, 3);
  assert.equal(families[1].primary.summary, DEPOSIT_PAGE);
});

test("the best-scoring member leads its family", () => {
  const families = collapseRecurring([
    result({ id: 1, period: "2023-12-31", summary: BOILERPLATE_2023, bm25_score: 15.73 }),
    result({ id: 2, period: "2021-12-31", summary: BOILERPLATE_2021, bm25_score: 15.33 }),
  ]);
  assert.equal(families[0].primary.id, 1);
});

test("never collapses across companies", () => {
  // Two banks restating the same regulatory language are two pieces of evidence.
  const families = collapseRecurring([
    result({ id: 1, entity: "MCB", period: "2023-12-31", summary: BOILERPLATE_2023 }),
    result({ id: 2, entity: "UBL", period: "2022-12-31", summary: BOILERPLATE_2021 }),
  ]);
  assert.equal(families.length, 2);
});

test("two pages from the same filing stay separate", () => {
  // Same period cannot be a different year of itself, however similar.
  const families = collapseRecurring([
    result({ id: 1, period: "2023-12-31", summary: BOILERPLATE_2023 }),
    result({ id: 2, period: "2023-12-31", summary: BOILERPLATE_2023 }),
  ]);
  assert.equal(families.length, 2);
});

test("family members are offered newest first", () => {
  const families = collapseRecurring([
    result({ id: 1, period: "2022-12-31", summary: BOILERPLATE_2023, bm25_score: 20 }),
    result({ id: 2, period: "2024-12-31", summary: BOILERPLATE_2024, bm25_score: 10 }),
  ]);
  assert.deepEqual(
    familyMembers(families[0]).map((entry) => entry.period),
    ["2024-12-31", "2022-12-31"],
  );
});

test("an empty summary never collapses into anything", () => {
  const families = collapseRecurring([
    result({ id: 1, period: "2023-12-31", summary: "" }),
    result({ id: 2, period: "2022-12-31", summary: "" }),
  ]);
  assert.equal(families.length, 2);
});
