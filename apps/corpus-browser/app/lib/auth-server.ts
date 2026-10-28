import { runtimeVariable } from "./runtime-env";

export interface AuthenticatedUser {
  id: string;
  email: string | null;
}

export async function authenticatedUser(request: Request): Promise<AuthenticatedUser | null> {
  const authorization = request.headers.get("authorization") || "";
  if (!authorization.startsWith("Bearer ")) return null;

  const [supabaseUrl, supabaseAnonKey] = await Promise.all([
    runtimeVariable("SUPABASE_URL"),
    runtimeVariable("SUPABASE_ANON_KEY"),
  ]);
  if (!supabaseUrl || !supabaseAnonKey) return null;

  try {
    const response = await fetch(`${supabaseUrl.replace(/\/$/, "")}/auth/v1/user`, {
      headers: {
        Authorization: authorization,
        apikey: supabaseAnonKey,
      },
      cache: "no-store",
    });
    if (!response.ok) return null;
    const payload = await response.json() as {
      id?: string;
      email?: string;
      is_anonymous?: boolean;
    };
    if (!payload.id || payload.is_anonymous) return null;
    return { id: payload.id, email: payload.email || null };
  } catch {
    return null;
  }
}

export async function requireApiUser(request: Request): Promise<Response | AuthenticatedUser> {
  const user = await authenticatedUser(request);
  if (user) return user;
  return Response.json(
    { error: "Sign in to use Ledger." },
    { status: 401, headers: { "Cache-Control": "no-store" } },
  );
}

export function isAuthResponse(value: Response | AuthenticatedUser): value is Response {
  return value instanceof Response;
}

export function forwardedAuthorization(request: Request): string {
  return request.headers.get("authorization") || "";
}
