import assert from "node:assert/strict";
import test from "node:test";
import { buildIndustryGraph, sourceGraphIndustries } from "../data/source-graph.ts";
import { readFile } from "node:fs/promises";

test("builds a fertilizer graph with the complete typed source chain", () => {
  const graph = buildIndustryGraph("fertilizer");
  const types = new Set(graph.nodes.map((node) => node.type));
  assert.deepEqual(types, new Set(["industry", "driver", "signal", "collection", "publisher"]));
  assert.ok(graph.nodes.some((node) => node.label.includes("Nfdc Monthly")));
  assert.ok(graph.nodes.some((node) => node.metadata.collection_ref === "pk_mnfsr_nfdc.monthly_fertilizer_reviews"));
});

test("source graph links publication families back to fertilizer report questions", async () => {
  const client = await readFile(new URL("../app/source-graph/source-graph-client.tsx", import.meta.url), "utf8");
  assert.match(client, /Used in fertilizer report/);
  assert.match(client, /section\.dependencies\.some/);
  assert.match(client, /industries\/fertilizer\/report\?section=/);
});

test("distinguishes catalog facts from analyst mappings", () => {
  const graph = buildIndustryGraph("fertilizer");
  const facts = graph.edges.filter((edge) => edge.trust === "catalog_fact");
  const mappings = graph.edges.filter((edge) => edge.trust === "analyst_mapping");
  assert.ok(facts.length > 0);
  assert.ok(mappings.length > 0);
  assert.ok(facts.every((edge) => edge.relation === "publishes"));
  assert.ok(mappings.every((edge) => edge.relation !== "publishes"));
});

test("covers every industry pack with at least one publication purpose", () => {
  assert.equal(sourceGraphIndustries.length, 23);
  for (const industry of sourceGraphIndustries) {
    const graph = buildIndustryGraph(industry.id);
    assert.ok(graph.nodes.some((node) => node.type === "signal"), industry.name);
  }
});
