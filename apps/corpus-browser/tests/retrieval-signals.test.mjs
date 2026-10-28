import assert from "node:assert/strict";
import test from "node:test";

import {
  buildTableEvidenceTitle,
  buildPhraseQuery,
  buildSimilarPrototype,
  deriveMatchedHeading,
  deriveSummaryTitle,
  extractPageSignals,
  selectEvidenceTitle,
} from "../api/retrieval-signals.ts";

test("buildPhraseQuery creates a real ordered FTS phrase", () => {
  assert.equal(
    buildPhraseQuery("Credit risk by industry sector"),
    '"credit risk by industry sector"',
  );
});

test("matched heading follows the query to the relevant table within a mixed page", () => {
  const heading = deriveMatchedHeading(`
# FEE AND COMMISSION INCOME

| Branch banking customer fees | 100 |

## 32. OPERATING EXPENSES

| Marketing, advertisement and publicity | 250 |
| Information technology expenses | 300 |
`, "marketing advertisement publicity");
  assert.equal(heading, "Marketing, advertisement and publicity");
});

test("page signals prefer the specific disclosure over a generic report heading", () => {
  const signals = extractPageSignals(`
# Notes to the Consolidated Financial Statements

for the year ended December 31, 2025

## 44.1.3 Advances

Credit risk by industry sector

| Gross advances | Non-performing advances | Credit loss allowance held |
| --- | --- | --- |
| Agriculture | 100 | 2 |
`);
  assert.equal(signals.title, "Credit risk by industry sector");
  assert.deepEqual(
    signals.tableSignature.slice(0, 3),
    ["Gross advances", "Non-performing advances", "Credit loss allowance held"],
  );
  assert.ok(signals.tableRows >= 2);
});

test("evidence title prefers the page cue that best describes the active query", () => {
  assert.equal(
    selectEvidenceTitle(
      [
        "4 FINANCIAL RISK MANAGEMENT",
        "5 CASH AND BALANCES WITH TREASURY BANKS",
        "FOR THE THREE MONTHS ENDED MARCH 31, 2026",
      ],
      "cash and bank balances",
    ),
    "5 CASH AND BALANCES WITH TREASURY BANKS",
  );
  assert.equal(
    selectEvidenceTitle(
      [
        "42 EMPLOYEES PROVIDENT FUND TRUST",
        "Metric Tons · Current installed capacity · Actual production",
      ],
      "installed capacity actual production",
    ),
    "Metric Tons · Current installed capacity · Actual production",
  );
  assert.equal(
    buildTableEvidenceTitle(
      [
        "Particulars",
        "FY 2021",
        "Installed capacity (cement)",
        "Actual production",
      ],
      "installed capacity actual production",
    ),
    "Installed capacity (cement) · Actual production",
  );
});

test("selected-page prototype uses headings and table keys, not the whole page", () => {
  const prototype = buildSimilarPrototype(
    `
NOTES TO THE FINANCIAL STATEMENTS

44 CAPITAL ADEQUACY, LEVERAGE RATIO & LIQUIDITY REQUIREMENTS

| CET 1 CAR | Tier 1 CAR | Total CAR |
| --- | --- | --- |
| 14.05% | 14.58% | 18.98% |
`,
    "capital adequacy CET1 tier 1 total CAR leverage ratio",
    "key capital ratios",
  );
  assert.match(prototype.heading, /CAPITAL ADEQUACY/i);
  assert.match(prototype.query, /capital/);
  assert.ok(prototype.query.split(/\s+/).length <= 10);
});

test("search summary supplies a query-aware title when Markdown has no heading", () => {
  assert.equal(
    deriveSummaryTitle(
      "Statement Note 30, cost of sales breakdown, raw materials consumed, salaries wages benefits, fuel and power, depreciation",
      "fuel and power",
    ),
    "Fuel and power",
  );
  assert.equal(
    deriveSummaryTitle(
      "Segment Information Note, fuel and lube segments, revenue breakdown, segment results after tax",
      "base oils segment revenue",
    ),
    "Segment Information",
  );
});
