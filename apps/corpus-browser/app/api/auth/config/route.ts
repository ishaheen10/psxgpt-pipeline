import { runtimeVariable } from "../../../lib/runtime-env";

export async function GET() {
  const [url, anonKey] = await Promise.all([
    runtimeVariable("SUPABASE_URL"),
    runtimeVariable("SUPABASE_ANON_KEY"),
  ]);
  return Response.json({
    configured: Boolean(url && anonKey),
    url: url || undefined,
    anonKey: anonKey || undefined,
  }, {
    headers: { "Cache-Control": "no-store" },
  });
}
