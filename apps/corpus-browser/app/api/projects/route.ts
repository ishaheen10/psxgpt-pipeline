import { corpusApiFetch } from "../../lib/corpus-api";
import {
  forwardedAuthorization,
  isAuthResponse,
  requireApiUser,
} from "../../lib/auth-server";

export async function GET(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const response = await corpusApiFetch("/api/projects", {
    headers: {
      Accept: "application/json",
      Authorization: forwardedAuthorization(request),
    },
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
