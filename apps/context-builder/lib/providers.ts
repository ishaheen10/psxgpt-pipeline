/**
 * Compile providers.
 *
 * Only providers whose key already exists in the pipeline's environment are
 * listed. There is no Anthropic or OpenAI key in this project, so those are not
 * offered rather than being offered and failing at send time.
 *
 * This module holds no secrets and is safe to import from the client. The keys
 * are read only in app/api/compile/route.ts.
 */

export type ProviderId = "deepseek" | "gemini" | "mistral";

export interface Provider {
  id: ProviderId;
  name: string;
  /** Env var that must be set for this provider to be usable. */
  keyVar: string;
  contextWindow: string;
  note: string;
  /**
   * Input-token rate used for the pre-send cost estimate, in USD per million
   * tokens. These are list-price approximations, not billing truth — correct
   * them to your contracted rates. The UI labels any number derived from them
   * as an estimate, and shows the provider's reported usage after the call.
   */
  usdPerMillionInput: number;
}

export const PROVIDERS: Provider[] = [
  {
    id: "deepseek",
    name: "DeepSeek V4 Pro",
    keyVar: "DEEPSEEK_API_KEY",
    contextWindow: "128k tokens",
    note: "Already the pipeline's review model; strongest on filing language",
    usdPerMillionInput: 0.28,
  },
  {
    id: "gemini",
    name: "Gemini 3.1 Flash Lite",
    keyVar: "GEMINI_API_KEY",
    contextWindow: "1M tokens",
    note: "Cheapest, and the only option that fits a very large stack",
    usdPerMillionInput: 0.1,
  },
  {
    id: "mistral",
    name: "Mistral Large",
    keyVar: "MISTRAL_API_KEY",
    contextWindow: "128k tokens",
    note: "Independent third opinion when two models disagree",
    usdPerMillionInput: 2.0,
  },
];

export const DEFAULT_PROVIDER: ProviderId = "deepseek";

export function providerById(id: string): Provider | undefined {
  return PROVIDERS.find((provider) => provider.id === id);
}

export function estimateCostUsd(provider: Provider, inputTokens: number): number {
  return (inputTokens / 1_000_000) * provider.usdPerMillionInput;
}

export function formatUsd(value: number): string {
  if (value === 0) return "$0.00";
  if (value < 0.01) return "<$0.01";
  return `$${value.toFixed(2)}`;
}

export interface CompileResponse {
  answer: string;
  provider: ProviderId;
  model: string;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
  error?: string;
}
