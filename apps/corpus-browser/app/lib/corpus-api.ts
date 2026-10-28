import { runtimeVariable } from "./runtime-env";
import corpusWorker from "../../api/worker";

const DEFAULT_CORPUS_API_URL =
  "https://psxgpt-corpus-api.isfandiyar-shaheen.workers.dev";

export async function corpusApiFetch(path: string, init?: RequestInit): Promise<Response> {
  try {
    const { env } = await import("cloudflare:workers");
    const bindings = env as unknown as Record<string, unknown>;
    // The structure-aware search path needs the main corpus database, source
    // bucket, and retrieval sidecar. A partial binding set can serve files but
    // must not execute the corpus worker in-process.
    if (bindings.DB && bindings.KEY_DB && bindings.SOURCE_BUCKET) {
      return corpusWorker.fetch(
        new Request(new URL(path, "https://corpus-api.internal"), init),
        bindings as never,
      );
    }
  } catch {
    // Node tests and Sites deployments without direct corpus bindings use HTTPS.
  }

  const baseUrl = await runtimeVariable("CORPUS_API_URL") || DEFAULT_CORPUS_API_URL;
  return fetch(new URL(path, baseUrl), init);
}
