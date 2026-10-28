import assert from "node:assert/strict";
import test from "node:test";
import { extractStructure } from "../scripts/build-structure-index.mjs";

test("extractStructure indexes Markdown headings and table headers without copying body prose", () => {
  const structure = extractStructure(`
# ACME LIMITED
## 17 DEPOSITS AND OTHER ACCOUNTS

This narrative mentions an unrelated acquisition.

| Customer type | 2025 | 2024 |
| --- | ---: | ---: |
| Individuals | 100 | 90 |

**Capital adequacy ratio**
`);

  assert.deepEqual(structure.headings, [
    "ACME LIMITED",
    "17 DEPOSITS AND OTHER ACCOUNTS",
    "Capital adequacy ratio",
  ]);
  assert.deepEqual(structure.table_headers, ["Customer type", "2025", "2024"]);
  assert.equal(structure.table_count, 1);
  assert.equal(structure.headings.includes("unrelated acquisition"), false);
});

test("extractStructure leaves missing structure empty instead of inventing labels", () => {
  const structure = extractStructure("A paragraph with ordinary prose and 2025 values.");
  assert.deepEqual(structure.headings, []);
  assert.deepEqual(structure.table_headers, []);
  assert.equal(structure.table_count, 0);
});
