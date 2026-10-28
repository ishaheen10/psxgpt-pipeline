import { proxyGet } from "@/lib/upstream";

export async function GET() {
  return proxyGet("/api/stats");
}
