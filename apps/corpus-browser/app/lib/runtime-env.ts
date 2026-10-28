export async function runtimeVariable(name: string): Promise<string | undefined> {
  try {
    const { env } = await import("cloudflare:workers");
    const binding = (env as unknown as Record<string, unknown>)[name];
    if (typeof binding === "string" && binding) return binding;
  } catch {
    // Node-based render tests have no cloudflare:workers module.
  }
  return process.env[name];
}
