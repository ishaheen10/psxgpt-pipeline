import { corpusApiFetch } from "../../lib/corpus-api";
import {
  forwardedAuthorization,
  isAuthResponse,
  requireApiUser,
} from "../../lib/auth-server";

export async function POST(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const body = await request.text();
  const response = await corpusApiFetch("/api/feedback", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: forwardedAuthorization(request),
    },
    body,
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}
