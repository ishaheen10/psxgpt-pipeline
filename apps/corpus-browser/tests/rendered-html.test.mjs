import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const appRoot = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: {
        accept: "text/html",
        host: "localhost",
        "x-forwarded-host": "localhost",
        "x-forwarded-proto": "http",
      },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the finished Ledger corpus browser", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Ledger — Authoritative Corpus Browser<\/title>/i);
  assert.match(html, /Search the source/i);
  assert.match(html, /Build the context/i);
  assert.match(html, /Connecting securely/i);
  assert.match(html, /same Supabase account used by psxGPT/i);
  assert.doesNotMatch(html, /Ticker and period filters/i);
  assert.match(html, /og\.png/i);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/i);
});

test("keeps the product surface free of starter scaffolding", async () => {
  const [page, layout, packageJson, retrievalExecution] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../app/lib/retrieval-execution.ts", import.meta.url), "utf8"),
  ]);

  assert.match(page, /analyst-context\.v2/);
  assert.match(retrievalExecution, /\/api\/search/);
  assert.match(page, /\/api\/document/);
  assert.match(page, /\/api\/route/);
  assert.match(page, /\/api\/compile/);
  assert.match(page, /\/api\/cart/);
  assert.match(page, /\/api\/history/);
  assert.match(page, /Refine results/);
  assert.match(page, /Saved to research cart/);
  assert.match(page, /Research trail/);
  assert.match(page, /Ranked pages from authoritative sources/);
  assert.match(page, /Compile answer from selected pages/);
  assert.match(page, /ReactMarkdown/);
  assert.match(page, /remarkGfm/);
  assert.match(page, /Download \.md/);
  assert.match(page, /className="markdown-reader"/);
  assert.doesNotMatch(page, /Add best pages to context/);
  assert.doesNotMatch(page, /One strong starting page for each company and evidence need/);
  assert.match(page, /Compile answer/);
  assert.match(page, /useLedgerAuth/);
  assert.doesNotMatch(page, /Evidence coverage/);
  assert.match(page, /Relevant passage/);
  assert.match(page, /Page contents/);
  assert.doesNotMatch(page, /recommendation-reason|recommendationLabel/);
  assert.match(page, /Read page/);
  assert.match(page, /View PDF page/);
  assert.match(page, /Add page to context/);
  assert.doesNotMatch(page, /Rate this result/);
  assert.doesNotMatch(page, /Find similar/);
  assert.doesNotMatch(page, /setSimilarBaseSearch|setSimilarCue|setSimilarLoading/);
  assert.match(page, /Copy bundle Markdown/);
  assert.doesNotMatch(page, /bearerToken|Analyst API|endpoint you control/);
  assert.match(page, /Adjust/);
  assert.match(layout, /generateMetadata/);
  assert.match(layout, /summary_large_image/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton|drizzle/);
  await access(new URL("../public/og.png", import.meta.url));
  await assert.rejects(access(new URL("../app/_sites-preview", import.meta.url)));
  await assert.rejects(access(new URL("public/favicon.svg", appRoot)));
});
