import assert from "node:assert/strict";
import test from "node:test";

import { assembleCoverageAwareResults } from "../api/result-assembly.ts";

function result(id, entity, score) {
  return {
    id,
    corpus: "filing",
    entity,
    source_name: entity,
    title: `${entity} evidence ${id}`,
    period: "2025-12-31",
    page_number: id,
    summary: "answer-bearing evidence",
    snippet: "answer-bearing evidence",
    bm25_score: score,
    source_url: null,
    thumbnail_url: null,
    asset_key: null,
    tags: [],
    quality_flags: [],
    evidence_score: score,
  };
}

test("coverage assembly preserves rank one then covers sufficiently supported required entities", () => {
  const ranked = [
    result(1, "MCB", 1),
    result(2, "MCB", 0.96),
    result(3, "MCB", 0.92),
    result(4, "UBL", 0.86),
    result(5, "HBL", 0.79),
    result(6, "ABL", 0.74),
  ];

  assert.deepEqual(
    assembleCoverageAwareResults(ranked, ["UBL", "HBL", "ABL", "MCB"], 6)
      .map((item) => `${item.entity}:${item.id}`),
    ["MCB:1", "UBL:4", "HBL:5", "ABL:6", "MCB:2", "MCB:3"],
  );
});

test("coverage assembly does not promote a weak company page merely to fill the grid", () => {
  const ranked = [
    result(1, "MCB", 1),
    result(2, "MCB", 0.95),
    result(3, "UBL", 0.8),
    result(4, "HBL", 0.7),
    result(5, "ABL", 0.4),
  ];

  assert.deepEqual(
    assembleCoverageAwareResults(ranked, ["UBL", "HBL", "ABL", "MCB"], 4)
      .map((item) => `${item.entity}:${item.id}`),
    ["MCB:1", "UBL:3", "HBL:4", "MCB:2"],
  );
});
