import { corpusApiFetch } from "../../lib/corpus-api";
import {
  forwardedAuthorization,
  isAuthResponse,
  requireApiUser,
} from "../../lib/auth-server";

function upstreamPath(request: Request): string {
  const incoming = new URL(request.url);
  const upstream = new URL("/api/history", "https://corpus-api.internal");
  const sessionId = incoming.searchParams.get("session_id");
  if (sessionId) upstream.searchParams.set("session_id", sessionId);
  return `${upstream.pathname}${upstream.search}`;
}

export async function GET(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const response = await corpusApiFetch(upstreamPath(request), {
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

export async function POST(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const response = await corpusApiFetch("/api/history", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: forwardedAuthorization(request),
    },
    body: await request.text(),
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
