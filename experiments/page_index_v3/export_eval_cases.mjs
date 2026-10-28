#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const sourcePath = new URL(
  "../../apps/corpus-browser/scripts/eval-retrieval-50.mjs",
  import.meta.url,
);
const source = await readFile(sourcePath, "utf8");
const boundary = source.indexOf("function evaluateResults");
if (boundary < 0) throw new Error("Could not locate eval case boundary");
const prefix = source.slice(0, boundary);
const code = `${prefix}
JSON.stringify(cases.map((testCase) => ({
  id: testCase.id,
  family: testCase.family,
  shape: testCase.shape,
  natural_query: testCase.naturalQuery,
  params: testCase.params,
  routed: testCase.routed,
  expected_entities: testCase.expectedEntities,
  min_relevant: testCase.minRelevant
})))`;
const serialized = vm.runInNewContext(code, {
  process: { argv: ["node", "eval-retrieval-50.mjs"] },
  URL,
  fetch,
  AbortSignal,
});
process.stdout.write(`${serialized}\n`);
