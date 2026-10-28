import { corpusApiFetch } from "../../lib/corpus-api";

function compact(value: string | null, limit: number) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
}

export async function GET(request: Request) {
  const incoming = new URL(request.url);
  const query = compact(incoming.searchParams.get("q"), 180);
  if (!query) return Response.json({ error: "Enter a search phrase." }, { status: 400 });

  const upstream = new URLSearchParams({
    q: query,
    q_literal: query,
    q_key: query,
    q_summary: query,
    q_text: query,
    q_heading: query,
    corpora: "filing,announcement,macro",
    limit: "6",
    retrieval_mode: "structure",
  });
  const ticker = compact(incoming.searchParams.get("ticker"), 16).toUpperCase();
  if (/^[A-Z0-9.-]{1,16}$/.test(ticker)) {
    upstream.set("tickers", ticker);
    upstream.set("required_tickers", ticker);
  }

  const response = await corpusApiFetch(`/api/search?${upstream}`, {
    headers: { Accept: "application/json" },
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
