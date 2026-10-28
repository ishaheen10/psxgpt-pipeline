import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("in-process corpus search requires the retrieval sidecar binding", async () => {
  const source = await readFile(new URL("../app/lib/corpus-api.ts", import.meta.url), "utf8");
  assert.match(source, /bindings\.DB && bindings\.KEY_DB && bindings\.SOURCE_BUCKET/);
  assert.match(source, /DEFAULT_CORPUS_API_URL/);
});
