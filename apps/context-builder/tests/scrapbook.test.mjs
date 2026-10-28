import assert from "node:assert/strict";
import test from "node:test";

import { fromClipped, toClipped, describeScrapbook, emptyScrapbook } from "../lib/scrapbook.ts";

const source = {
  id: 42,
  corpus: "filing",
  entity: "UBL",
  source_name: "UBL",
  title: "UBL — page 217",
  period: "2021-12-31",
  page_number: 217,
  summary: "deposits 1.89 trillion rupees current accounts savings deposits",
  snippet: "… ⟦deposits⟧ …",
  bm25_score: 22.16,
  source_url: "https://source.psxgpt.com/PDF_PAGES/UBL/2021/page_217.pdf",
  thumbnail_url: null,
  asset_key: "PDF_PAGES/UBL/2021/page_217.pdf",
  tags: [],
  quality_flags: [],
  loading: false,
  inclusionMode: "summary",
  detail: {
    id: 42,
    corpus: "filing",
    text: "A very long page body that should never reach storage.",
    search_text: "deposits",
    source_url: null,
    thumbnail_url: null,
    asset: { key: "k", status: "available", bytes: 1, etag: null, uploaded_at: null },
    quality_flags: [],
  },
};

test("a clip stores identity and choices, never page text", () => {
  const clipped = toClipped(source);
  const serialized = JSON.stringify(clipped);
  assert.doesNotMatch(serialized, /very long page body/);
  assert.equal("detail" in clipped, false);
  assert.equal(clipped.inclusionMode, "summary");
  assert.equal(clipped.page_number, 217);
});

test("restoring a clip preserves the researcher's inclusion choice", () => {
  const restored = fromClipped(toClipped(source));
  assert.equal(restored.inclusionMode, "summary");
  assert.equal(restored.id, 42);
  assert.equal(restored.entity, "UBL");
  assert.equal(restored.bm25_score, 22.16);
});

test("a restored clip is marked loading until its text is refetched", () => {
  const restored = fromClipped(toClipped(source));
  // Claiming to hold full text that was never stored would silently send an
  // empty source to the model.
  assert.equal(restored.loading, true);
  assert.equal(restored.detail, undefined);
});

test("describeScrapbook summarises size and entities", () => {
  const book = emptyScrapbook("Test");
  assert.equal(describeScrapbook(book), "empty");
  book.pages = [toClipped(source), toClipped({ ...source, id: 43, entity: "MCB" })];
  assert.equal(describeScrapbook(book), "2 pages · UBL, MCB");
});

test("a new scrapbook has a distinct id", () => {
  assert.notEqual(emptyScrapbook().id, emptyScrapbook().id);
});
