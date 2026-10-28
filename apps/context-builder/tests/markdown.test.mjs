import assert from "node:assert/strict";
import test from "node:test";

import { isNumericCell, parseBlocks, parseInline } from "../lib/markdown-parse.ts";

// A real fragment from UBL's 2021 consolidated accounts, note 18.
const PAGE = `<!-- Page 217 -->

Notes to and forming part of the Consolidated Financial Statements

**18. DEPOSITS AND OTHER ACCOUNTS**

| | 2021 | 2020 |
| --- | ---: | ---: |
| **Customers** | | |
| Current deposits | 764,049,776 | 621,288,010 |
| Savings deposits | 628,434,262 | 520,116,881 |
| Total | 1,885,771,572 | 1,632,266,142 |`;

test("drops the parser's page-number comment", () => {
  const blocks = parseBlocks(PAGE);
  assert.equal(
    blocks.some((block) => JSON.stringify(block).includes("Page 217")),
    false,
  );
});

test("parses a financial table into header and rows", () => {
  const table = parseBlocks(PAGE).find((block) => block.kind === "table");
  assert.ok(table);
  assert.deepEqual(table.header, ["", "2021", "2020"]);
  assert.equal(table.rows.length, 4);
  assert.deepEqual(table.rows[1], ["Current deposits", "764,049,776", "621,288,010"]);
  assert.equal(table.width, 3);
});

test("a bold line becomes a paragraph, not a heading", () => {
  const blocks = parseBlocks(PAGE);
  const paragraph = blocks.find(
    (block) => block.kind === "paragraph" && block.text.includes("DEPOSITS AND OTHER ACCOUNTS"),
  );
  assert.ok(paragraph);
});

test("pipes without a divider row stay prose", () => {
  // Otherwise a sentence containing a pipe would silently become a table.
  const blocks = parseBlocks("| this is not | really a table |");
  assert.equal(blocks[0].kind, "paragraph");
});

test("parses headings at their level", () => {
  const blocks = parseBlocks("# Title\n\n### Sub");
  assert.deepEqual(blocks[0], { kind: "heading", level: 1, text: "Title" });
  assert.equal(blocks[1].level, 3);
});

test("parses lists, keeping ordered and unordered apart", () => {
  const unordered = parseBlocks("* one\n* two");
  assert.deepEqual(unordered[0], { kind: "list", ordered: false, items: ["one", "two"] });
  const ordered = parseBlocks("1. first\n2. second");
  assert.equal(ordered[0].ordered, true);
  assert.deepEqual(ordered[0].items, ["first", "second"]);
});

test("tokenises emphasis without letting italics eat bold", () => {
  assert.deepEqual(parseInline("**bold** and *it*"), [
    { kind: "strong", value: "bold" },
    { kind: "text", value: " and " },
    { kind: "em", value: "it" },
  ]);
});

test("unescapes the backslash sequences this OCR output contains", () => {
  // "AF Ferguson \& Co" appears throughout these filings.
  assert.deepEqual(parseInline("AF Ferguson \\& Co"), [
    { kind: "text", value: "AF Ferguson & Co" },
  ]);
});

test("unwraps the LaTeX artifacts the OCR emits around figures", () => {
  // Shareholding tables come through as `$51.60 \%$` for a filing that says
  // "51.60%". Rendered raw, every percentage on the page looks corrupted.
  assert.deepEqual(parseInline("$51.60 \\%$"), [{ kind: "text", value: "51.60 %" }]);
  // A single delimiter is a currency symbol, not a wrapper.
  assert.deepEqual(parseInline("$51.60"), [{ kind: "text", value: "$51.60" }]);
});

test("recognises figures so they can be right-aligned", () => {
  assert.equal(isNumericCell("764,049,776"), true);
  assert.equal(isNumericCell("(1,234)"), true);
  assert.equal(isNumericCell("12.39%"), true);
  assert.equal(isNumericCell("$51.60 \\%$"), true);
  assert.equal(isNumericCell("Current deposits"), false);
  assert.equal(isNumericCell("—"), false);
});

test("consecutive prose lines join into one paragraph", () => {
  const blocks = parseBlocks("line one\nline two\n\nsecond para");
  assert.equal(blocks.length, 2);
  assert.equal(blocks[0].text, "line one line two");
});
