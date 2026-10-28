import { SEARCH_PARAMS, forwardParams } from "@/lib/search-params";
import { proxyGet } from "@/lib/upstream";

export async function GET(request: Request) {
  const incoming = new URL(request.url);
  const upstream = forwardParams(incoming, new URL("http://placeholder/"), SEARCH_PARAMS);
  return proxyGet("/api/search", upstream.searchParams);
}
