import { PROVIDERS } from "@/lib/providers";
import { runtimeVariable } from "@/lib/runtime-env";

/**
 * Reports which providers actually have a key in this environment, so the UI
 * can disable the ones that would fail at send time instead of offering them.
 * Only the boolean is exposed, never the key.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  const providers = await Promise.all(
    PROVIDERS.map(async (provider) => ({
      id: provider.id,
      configured: Boolean(await runtimeVariable(provider.keyVar)),
    })),
  );
  return Response.json({ providers });
}
