import assert from "node:assert/strict";
import test from "node:test";

import { inferSearch, resolveDates } from "../lib/query.ts";
import { forwardParams, SEARCH_PARAMS } from "../lib/search-params.ts";
import { buildContextMarkdown, estimateTokens, selectedContent } from "../lib/context.ts";
import { formatScore } from "../lib/format.ts";

const KNOWN = new Set(["UBL", "HBL", "ABL", "HUBCO"]);

function infer(rawQuery, overrides = {}) {
  return inferSearch({
    rawQuery,
    selectedTickers: [],
    knownTickers: KNOWN,
    periodPreset: "all",
    fromYear: "2024",
    toYear: "2026",
    selectedCorpora: ["filing", "announcement", "macro"],
    currentYear: 2026,
    ...overrides,
  });
}

test("lifts known tickers out of the query and into scope", () => {
  const result = infer("Financial statements of UBL, HBL and ABL last 3 years");
  assert.deepEqual(result.tickers, ["UBL", "HBL", "ABL"]);
  // Tickers, stopwords, and the period phrase must not survive into BM25 terms.
  assert.doesNotMatch(result.lexicalQuery, /UBL|HBL|ABL|last 3 years|\bof\b|\band\b/i);
  assert.match(result.lexicalQuery, /financial statements/i);
});

test("reads a relative period into an absolute range", () => {
  const result = infer("UBL deposits last 3 years");
  assert.equal(result.dateFrom, "2024-01-01");
  assert.equal(result.dateTo, "2026-12-31");
});

test("does not treat an ordinary capitalized word as a ticker", () => {
  const result = infer("Deposits growth in Pakistan");
  assert.deepEqual(result.tickers, []);
});

test("narrows corpus on filing intent only while the corpus is untouched", () => {
  assert.deepEqual(infer("annual reports of HUBCO").corpora, ["filing"]);
  // An explicit choice by the researcher wins over inferred intent.
  assert.deepEqual(
    infer("annual reports of HUBCO", { selectedCorpora: ["macro"] }).corpora,
    ["macro"],
  );
});

test("never yields an empty BM25 query", () => {
  const result = infer("UBL");
  assert.ok(result.lexicalQuery.trim().length > 0);
});

test("resolveDates leaves an all-periods search unbounded", () => {
  assert.deepEqual(resolveDates("all", "2024", "2026", 2026), { dateFrom: null, dateTo: null });
});

test("the search proxy forwards every filter the worker understands", () => {
  const incoming = new URL(
    "http://app/api/search?q=deposits&limit=6&tickers=UBL,HBL&corpora=filing&date_from=2024-01-01&date_to=2026-12-31",
  );
  const forwarded = forwardParams(incoming, new URL("http://placeholder/"), SEARCH_PARAMS);

  // Regression guard. The corpus-browser proxy forwards only q and limit, so
  // its ticker, corpus, and date controls have no effect on the query.
  assert.equal(forwarded.searchParams.get("tickers"), "UBL,HBL");
  assert.equal(forwarded.searchParams.get("corpora"), "filing");
  assert.equal(forwarded.searchParams.get("date_from"), "2024-01-01");
  assert.equal(forwarded.searchParams.get("date_to"), "2026-12-31");
  assert.equal(forwarded.searchParams.get("q"), "deposits");
});

test("the proxy omits params the caller never set", () => {
  const forwarded = forwardParams(
    new URL("http://app/api/search?q=deposits"),
    new URL("http://placeholder/"),
    SEARCH_PARAMS,
  );
  assert.equal(forwarded.searchParams.has("tickers"), false);
  assert.equal(forwarded.searchParams.has("date_from"), false);
});

const source = {
  id: 1,
  corpus: "filing",
  entity: "UBL",
  source_name: "UBL",
  title: "Quarterly report — page 42",
  period: "2023-03-31",
  page_number: 42,
  summary: "deposits 197.7 billion customers 167.7 billion",
  snippet: "… ⟦Customers⟧ current ⟦deposits⟧ …",
  bm25_score: 7.14,
  source_url: "https://source.psxgpt.com/PDF_PAGES/UBL/2023/page_042.pdf",
  thumbnail_url: null,
  asset_key: "PDF_PAGES/UBL/2023/page_042.pdf",
  tags: [],
  quality_flags: [],
  loading: false,
  inclusionMode: "full",
  detail: {
    id: 1,
    corpus: "filing",
    text: "Full page markdown body.",
    search_text: "deposits 197.7 billion",
    source_url: null,
    thumbnail_url: null,
    asset: { key: "k", status: "available", bytes: 1, etag: null, uploaded_at: null },
    quality_flags: [],
  },
};

test("inclusion mode decides what a source contributes", () => {
  assert.equal(selectedContent(source), "Full page markdown body.");
  assert.equal(
    selectedContent({ ...source, inclusionMode: "summary" }),
    "deposits 197.7 billion customers 167.7 billion",
  );
});

test("context markdown carries provenance next to the text", () => {
  const markdown = buildContextMarkdown("Session", "What are deposits?", [source]);
  assert.match(markdown, /## Source 1 — UBL: Quarterly report/);
  assert.match(markdown, /Stored object: available/);
  assert.match(markdown, /BM25 rank score: 7\.14/);
  assert.match(markdown, /Cite the source number/);
  assert.match(markdown, /Full page markdown body\./);
});

test("context markdown states answer scope and coverage", () => {
  const markdown = buildContextMarkdown("Session", "What are deposits?", [source]);
  // Mirrors ANSWER SCOPE / CONTEXT COVERAGE in the serving engine's payload.
  assert.match(markdown, /## Answer scope/);
  assert.match(markdown, /Retrieved scope: UBL \(1 entity\)/);
  assert.match(markdown, /Do not discuss, rank, value, or compare anything outside this scope/);
  assert.match(markdown, /## Context coverage/);
  assert.match(markdown, /- UBL: 1 page; periods 2023-03-31/);
});

test("token estimate scales with characters", () => {
  assert.equal(estimateTokens(4000), 1000);
});

test("a tiny BM25 score never renders as 0.00", () => {
  // Real scores for common terms across 256k pages land near 4e-6. Rounding
  // those to "0.00" reads as "no match" for the top-ranked page.
  assert.equal(formatScore(4.13118820666411e-6), "4.1e-6");
  assert.equal(formatScore(7.145478), "7.15");
  assert.equal(formatScore(0), "0");
});
