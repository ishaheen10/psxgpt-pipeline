import { corpusApiFetch } from "../../lib/corpus-api";
import { isAuthResponse, requireApiUser } from "../../lib/auth-server";

export async function GET(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const incoming = new URL(request.url);
  const upstream = new URL("/api/document", "https://corpus-api.internal");
  upstream.searchParams.set("corpus", incoming.searchParams.get("corpus") || "");
  upstream.searchParams.set("id", incoming.searchParams.get("id") || "");

  const response = await corpusApiFetch(`${upstream.pathname}${upstream.search}`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": "application/json" },
  });
}
