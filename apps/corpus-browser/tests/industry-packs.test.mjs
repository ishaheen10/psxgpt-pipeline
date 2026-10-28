import assert from "node:assert/strict";
import test from "node:test";

import { readFile } from "node:fs/promises";

const registry = JSON.parse(await readFile(new URL("../data/industry-packs.json", import.meta.url), "utf8"));

test("publishes one direct pack per canonical public-company industry", () => {
  assert.equal(registry.schema_version, "ledger-industry-feed-registry.v2");
  assert.equal(registry.packs.length, 23);
  assert.equal(new Set(registry.packs.map((pack) => pack.id)).size, 23);
  assert.equal(registry.packs.flatMap((pack) => pack.feeds).length, 99);
  const signalIds = registry.packs.flatMap((pack) => pack.feeds.map((feed) => `${pack.id}:${feed.signal_id}`));
  assert.equal(new Set(signalIds).size, signalIds.length);
  assert.ok(signalIds.every((id) => !id.endsWith(":")));
});

test("carries stage health separately from selective parse planning", () => {
  assert.equal(registry.health_snapshot.direct_pack_count, 23);
  assert.equal(registry.health_snapshot.scheduler_telemetry, "unknown");
  assert.match(registry.health_snapshot.denominator_note, /763-document plan/);
  assert.ok(registry.packs.every((pack) => pack.health?.stages?.boundary));
  assert.ok(registry.packs.every((pack) => pack.health?.execution_status === "not_started"));
});

test("keeps readiness honest at feed level", () => {
  const fertilizer = registry.packs.find((pack) => pack.id === "fertilizer");
  assert.ok(fertilizer);
  assert.match(fertilizer.economic_identity, /inventory|feedstock/i);
  assert.ok(fertilizer.feeds.some((feed) => feed.deployment_state === "parse_candidate"));
  assert.ok(fertilizer.feeds.some((feed) => feed.deployment_state === "blocked"));
  assert.ok(fertilizer.feeds.some((feed) => feed.deployment_state === "partial_source_coverage"));
});

test("ranks shared acquisition gaps by cross-industry leverage", () => {
  assert.equal(registry.priority_gaps[0].signal_id, "ogra_prices_rules");
  assert.equal(registry.priority_gaps[0].industry_count, 8);
  assert.ok(registry.priority_gaps.some((gap) => gap.signal_id === "secp_regulatory_events"));
});
