import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const dataPath = new URL("../data/fertilizer-report.ts", import.meta.url);
const workspacePath = new URL("../data/fertilizer-workspace.ts", import.meta.url);
const clientPath = new URL("../app/industries/fertilizer/report/report-client.tsx", import.meta.url);
const generatedPath = new URL("../data/fertilizer-report.generated.json", import.meta.url);

test("fertilizer report declares all five selectable context boundaries", async () => {
  const source = await readFile(dataPath, "utf8");
  for (const boundary of ["sector_view", "company_view", "thesis", "monitor", "evidence_pages"]) {
    assert.match(source, new RegExp(`\\| \\\"${boundary}\\\"|${boundary}:`));
  }
});

test("fertilizer report keeps the 100-page evidence pack complete and explicit", async () => {
  const source = await readFile(dataPath, "utf8");
  const pageCounts = [...source.matchAll(/estimatedPages: (\d+),/g)]
    .map((match) => Number(match[1]));
  const evidenceStart = source.indexOf("export const evidence");
  const sectorStart = source.indexOf("export const sectorView");
  const evidenceSource = source.slice(evidenceStart, sectorStart);
  const evidencePageCounts = [...evidenceSource.matchAll(/estimatedPages: (\d+),/g)]
    .map((match) => Number(match[1]));

  assert.equal(evidencePageCounts.reduce((sum, value) => sum + value, 0), 100);
  assert.ok(pageCounts.length > evidencePageCounts.length, "expected non-evidence context units too");
  assert.match(evidenceSource, /availability: "searchable"/);
  assert.match(evidenceSource, /availability: "pack_only"/);
});

test("report supports segment generation and assembled Markdown output", async () => {
  const client = await readFile(clientPath, "utf8");
  assert.match(client, /function generateSection/);
  assert.match(client, /function generateRemaining/);
  assert.match(client, /Report outline/);
  assert.match(client, /Generate segment/);
  assert.match(client, /Assembled report/);
  assert.match(client, /<Markdown value={activeSection.answer}/);
  assert.match(client, /localStorage/);
  assert.doesNotMatch(client, /Analyze fertilizer evidence/);
});

test("workspace recipes and monitoring streams resolve to physical evidence pages", async () => {
  const generated = JSON.parse(await readFile(generatedPath, "utf8"));
  const workspace = await import(workspacePath.href);
  const knownPages = new Set(Object.keys(generated.source_index));

  assert.equal(workspace.drivers.length, 6);
  assert.ok(workspace.contextRecipes.length >= 4);
  assert.ok(workspace.monitoringStreams.length >= 6);

  const selectedPages = [
    ...workspace.drivers.flatMap((driver) => driver.pageIds),
    ...workspace.drivers.flatMap((driver) => driver.indicators.flatMap((indicator) => indicator.pageIds)),
    ...workspace.contextRecipes.flatMap((recipe) => recipe.pageIds),
  ];
  assert.ok(selectedPages.length > 0);
  assert.ok(selectedPages.every((pageId) => knownPages.has(pageId)));
  assert.ok(workspace.drivers.every((driver) => driver.indicators.length >= 2));
});

test("generated report exposes all questions and physical-page provenance", async () => {
  const generated = JSON.parse(await readFile(generatedPath, "utf8"));
  assert.equal(generated.sections.filter((section) => section.kind === "atomic").length, 9);
  assert.equal(Object.keys(generated.source_index).length, 100);
  assert.equal(generated.sections.find((section) => section.id === "executive_summary").upstream.length, 11);
  assert.ok(generated.sections.every((section) => /\[P\d{3}\]/.test(section.answer)));
});
