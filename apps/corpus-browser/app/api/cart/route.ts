import { corpusApiFetch } from "../../lib/corpus-api";
import {
  forwardedAuthorization,
  isAuthResponse,
  requireApiUser,
} from "../../lib/auth-server";

function upstreamPath(request: Request): string {
  const incoming = new URL(request.url);
  const upstream = new URL("/api/cart", "https://corpus-api.internal");
  for (const parameter of ["session_id", "corpus", "page_id"]) {
    const value = incoming.searchParams.get(parameter);
    if (value) upstream.searchParams.set(parameter, value);
  }
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
  const response = await corpusApiFetch("/api/cart", {
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

export async function DELETE(request: Request) {
  const auth = await requireApiUser(request);
  if (isAuthResponse(auth)) return auth;
  const response = await corpusApiFetch(upstreamPath(request), {
    method: "DELETE",
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
