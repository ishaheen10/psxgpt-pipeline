/**
 * Reads configuration in a way that works on both runtimes this app runs on.
 *
 * On Cloudflare, vars and secrets arrive as bindings on the `cloudflare:workers`
 * env, not on `process.env`. Reading `process.env` alone works in `vinext dev`
 * and in Node tests but silently returns undefined for a deployed secret, which
 * would surface as "provider not configured" on a correctly configured deploy.
 */
export async function runtimeVariable(name: string): Promise<string | undefined> {
  try {
    const { env } = await import("cloudflare:workers");
    const binding = (env as unknown as Record<string, unknown>)[name];
    if (typeof binding === "string" && binding) return binding;
  } catch {
    // Not running on workerd; fall through to process.env.
  }
  return process.env[name];
}
