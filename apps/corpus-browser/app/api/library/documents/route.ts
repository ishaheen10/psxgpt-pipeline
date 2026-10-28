import { corpusApiFetch } from "../../../lib/corpus-api";
import { isAuthResponse, requireApiUser } from "../../../lib/auth-server";

const FORWARDED_PARAMETERS = new Set([
  "collection_ref",
  "q",
  "parse_coverage",
  "year",
  "limit",
  "offset",
]);

export async function GET(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const incoming = new URL(request.url);
  const upstream = new URL("/api/library/documents", "https://corpus-api.internal");
  for (const [name, value] of incoming.searchParams) {
    if (FORWARDED_PARAMETERS.has(name) && value) upstream.searchParams.set(name, value);
  }
  const response = await corpusApiFetch(`${upstream.pathname}${upstream.search}`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
