import { corpusApiFetch } from "../../lib/corpus-api";
import { isAuthResponse, requireApiUser } from "../../lib/auth-server";

export async function GET(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const incoming = new URL(request.url);
  const upstream = new URL("/api/search", "https://corpus-api.internal");
  for (const parameter of [
    "q",
    "q_literal",
    "q_key",
    "q_summary",
    "q_text",
    "q_heading",
    "limit",
    "tickers",
    "required_tickers",
    "industries",
    "years",
    "date_from",
    "date_to",
    "corpora",
    "filing_cadence",
    "latest",
    "prefer_recent",
    "query_pack",
    "debug",
    "retrieval_mode",
  ]) {
    const value = incoming.searchParams.get(parameter);
    if (value) upstream.searchParams.set(parameter, value);
  }

  const response = await corpusApiFetch(`${upstream.pathname}${upstream.search}`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": "application/json" },
  });
}
