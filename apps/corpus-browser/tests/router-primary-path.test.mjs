import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { textContainsExactSpan } from "../app/lib/text-grounding.ts";

const routeSource = await readFile(new URL("../app/api/route/route.ts", import.meta.url), "utf8");
const pageSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

test("natural-language routing has one model-planned path", () => {
  assert.equal(routeSource.includes("new RegExp"), false);
  assert.equal(routeSource.includes(".match("), false);
  assert.equal(routeSource.includes("matchAll"), false);
  assert.equal(routeSource.includes("fallback"), false);
  assert.equal(routeSource.includes("modelRouteErrors"), false);
  assert.equal(routeSource.includes("Correct the invalid retrieval plan"), false);
  assert.equal(routeSource.includes("for (let attempt"), false);
  assert.equal(routeSource.includes("normalizeModelRoute"), true);
  assert.equal(routeSource.includes('routedBy: "router"'), true);
});

test("the router plans for direct answer-bearing evidence without invented workstreams", () => {
  assert.equal(
    routeSource.includes("Optimize for pages that contain the evidence needed to answer the request"),
    true,
  );
  assert.equal(
    routeSource.includes("Evidence needs are only the independently answerable outputs the user requested"),
    true,
  );
  assert.equal(
    routeSource.includes("Do not add a filing facet unless the user separately requests later financial impact"),
    true,
  );
  assert.equal(
    routeSource.includes("The heading query must target the most specific likely answer-bearing heading"),
    true,
  );
});

test("the browser does not synthesize a second search plan", () => {
  assert.equal(pageSource.includes("inferSearch"), false);
  assert.equal(pageSource.includes('"fallback"'), false);
  assert.equal(pageSource.includes("Search could not be planned reliably"), false);
  assert.equal(pageSource.includes('routedBy: "manual"'), true);
});

test("entity scope preserves a quoted user mention without requiring a legal-name match", () => {
  assert.equal(
    textContainsExactSpan(
      "for the past 5 years, tell me the segment breakdown of NRLs base oil segment",
      "NRLs",
    ),
    true,
  );
  assert.equal(
    textContainsExactSpan("compare deposits", "National Refinery"),
    false,
  );
  assert.equal(
    routeSource.includes(
      "Use your judgment to map common market names, abbreviations, possessives, historical names, and minor spelling differences",
    ),
    true,
  );
});

test("facet decisions are authoritative rather than rejected by duplicated route fields", () => {
  assert.equal(
    routeSource.includes(
      "The facet plan is authoritative. The application derives route-level corpora",
    ),
    true,
  );
  assert.equal(routeSource.includes("Route-level corpora must equal the union"), false);
  assert.equal(routeSource.includes("Inferred company-industry scope must use filing"), false);
});
