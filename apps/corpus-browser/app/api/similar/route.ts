import { corpusApiFetch } from "../../lib/corpus-api";
import { isAuthResponse, requireApiUser } from "../../lib/auth-server";

export async function GET(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const incoming = new URL(request.url);
  const upstream = new URL("/api/similar", "https://corpus-api.internal");
  for (const parameter of [
    "corpus",
    "id",
    "mode",
    "limit",
    "active_query",
    "tickers",
    "industries",
    "years",
    "date_from",
    "date_to",
    "filing_cadence",
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
