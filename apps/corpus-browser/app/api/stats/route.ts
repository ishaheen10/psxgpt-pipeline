import { corpusApiFetch } from "../../lib/corpus-api";
import { isAuthResponse, requireApiUser } from "../../lib/auth-server";

export async function GET(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const response = await corpusApiFetch("/api/stats", {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": "application/json" },
  });
}
