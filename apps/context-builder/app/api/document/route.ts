import { DOCUMENT_PARAMS, forwardParams } from "@/lib/search-params";
import { proxyGet } from "@/lib/upstream";

export async function GET(request: Request) {
  const incoming = new URL(request.url);
  const upstream = forwardParams(incoming, new URL("http://placeholder/"), DOCUMENT_PARAMS);
  return proxyGet("/api/document", upstream.searchParams);
}
