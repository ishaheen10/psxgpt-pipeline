import assert from "node:assert/strict";
import test from "node:test";

import {
  buildScrapbookFile,
  buildSourceList,
  mergeScrapbooks,
  parseScrapbookFile,
  slugify,
} from "../lib/export.ts";
import { emptyScrapbook } from "../lib/scrapbook.ts";

const sources = [
  {
    entity: "UBL",
    title: "UBL — page 217",
    period: "2021-12-31",
    source_url: "https://source.psxgpt.com/PDF_PAGES/UBL/2021/page_217.pdf",
  },
  { entity: "MCB", title: "MCB — page 23", period: "2025-03-31", source_url: null },
];

test("the citation list numbers sources to match their context numbers", () => {
  const list = buildSourceList("UBL deposit study", sources);
  assert.match(list, /1\. UBL — UBL — page 217 \(2021-12-31\)/);
  assert.match(list, /2\. MCB — MCB — page 23 \(2025-03-31\)/);
  assert.match(list, /source\.psxgpt\.com/);
});

test("a source with no URL still appears, without a dangling link", () => {
  const list = buildSourceList("s", [sources[1]]);
  assert.match(list, /MCB — page 23/);
  assert.doesNotMatch(list, /undefined|null/);
});

test("slugify produces a safe filename and never an empty one", () => {
  assert.equal(slugify("UBL deposit study"), "ubl-deposit-study");
  assert.equal(slugify("  ***  "), "scrapbook");
  assert.equal(slugify("a".repeat(200)).length, 60);
});

test("an exported file round-trips", () => {
  const book = emptyScrapbook("Round trip");
  const restored = parseScrapbookFile(buildScrapbookFile([book]));
  assert.equal(restored.length, 1);
  assert.equal(restored[0].name, "Round trip");
});

test("a foreign or malformed file imports nothing rather than throwing", () => {
  assert.deepEqual(parseScrapbookFile("not json"), []);
  assert.deepEqual(parseScrapbookFile(JSON.stringify({ format: "something-else" })), []);
  assert.deepEqual(parseScrapbookFile(JSON.stringify({ format: "ledger.scrapbook" })), []);
});

test("re-importing the same file does not overwrite the existing scrapbook", () => {
  const book = emptyScrapbook("Shared pack");
  const existing = [{ ...book, name: "Shared pack, since edited" }];
  const merged = mergeScrapbooks(existing, [book]);
  assert.equal(merged.length, 2);
  // The edited original survives untouched; the incoming copy is re-identified.
  assert.ok(merged.some((entry) => entry.name === "Shared pack, since edited"));
  assert.ok(merged.some((entry) => entry.name === "Shared pack (imported)"));
  assert.notEqual(merged[0].id, merged[1].id);
});

test("importing a genuinely new scrapbook keeps its id", () => {
  const existing = [emptyScrapbook("A")];
  const incoming = emptyScrapbook("B");
  const merged = mergeScrapbooks(existing, [incoming]);
  assert.equal(merged.find((entry) => entry.name === "B").id, incoming.id);
});
