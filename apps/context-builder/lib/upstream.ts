/**
 * Server-side client for the read-only corpus API.
 *
 * The corpus Worker is deployed separately (apps/corpus-browser/api/worker.ts,
 * `wrangler.api.toml`) and is read-only by construction: GET only, SELECT only,
 * R2 head only. This app proxies to it rather than re-binding D1 and R2, so the
 * production corpus keeps one access path.
 */
import { runtimeVariable } from "./runtime-env";

const DEFAULT_CORPUS_API_URL = "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev";

export async function corpusApiUrl(): Promise<string> {
  return (await runtimeVariable("CORPUS_API_URL")) || DEFAULT_CORPUS_API_URL;
}

/** Proxies a GET to the corpus API, preserving its status and JSON body. */
export async function proxyGet(path: string, search?: URLSearchParams): Promise<Response> {
  const upstream = new URL(path, await corpusApiUrl());
  if (search) upstream.search = search.toString();

  try {
    const response = await fetch(upstream, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    return new Response(response.body, {
      status: response.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (error) {
    return Response.json(
      {
        error: "The corpus API is unreachable.",
        detail: error instanceof Error ? error.message : String(error),
      },
      { status: 502 },
    );
  }
}
