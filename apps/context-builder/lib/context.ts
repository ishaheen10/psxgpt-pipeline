import { formatScore } from "./format.ts";
import { coverage } from "./shaping.ts";
import type { QualityFlag, StackedSource } from "./corpus";

/**
 * Per-source ceiling on included characters. A single filing page is normally
 * well under this; the cap exists so one unusually long page cannot silently
 * consume the whole context budget.
 */
export const MAX_SOURCE_CHARS = 12_000;

export const CONTEXT_SCHEMA_VERSION = "analyst-context.v2";

export interface AppliedSearch {
  naturalQuery: string;
  lexicalQuery: string;
  tickers: string[];
  dateFrom: string | null;
  dateTo: string | null;
  corpora: string[];
}

/**
 * Rough token estimate at ~4 characters per token. Displayed as an estimate
 * only; `/api/compile` returns the provider's real usage after the call.
 */
export function estimateTokens(characters: number): number {
  return Math.ceil(characters / 4);
}

/** The text a source actually contributes, honoring its inclusion mode. */
export function selectedContent(source: StackedSource): string {
  const text =
    source.inclusionMode === "full" && source.detail?.text
      ? source.detail.text
      : source.summary || source.snippet;
  if (text.length <= MAX_SOURCE_CHARS) return text;
  return `${text.slice(0, MAX_SOURCE_CHARS)}\n\n[Source truncated at ${MAX_SOURCE_CHARS.toLocaleString()} characters]`;
}

function flagLine(flag: QualityFlag): string {
  const parts = [
    flag.severity ? flag.severity.toUpperCase() : "FLAG",
    flag.rule || flag.metric || flag.category || flag.flag_id,
    flag.message || "",
  ].filter(Boolean);
  return `  - ${parts.join(" · ")}`;
}

/**
 * Renders the stack as the markdown that is sent to the model.
 *
 * Provenance is deliberately inline rather than in a trailing appendix: the
 * model is asked to cite source numbers, so the number, the period, the R2
 * status, and the open deterministic checks must sit next to the text they
 * describe.
 */
export function buildContextMarkdown(
  sessionLabel: string,
  prompt: string,
  sources: StackedSource[],
): string {
  const blocks = sources.map((source, index) => {
    const flags = source.detail?.quality_flags || source.quality_flags || [];
    const header = [
      `## Source ${index + 1} — ${source.entity}: ${source.title}`,
      `- Corpus: ${source.corpus}`,
      `- Period: ${source.period || "not stated"}`,
      source.page_number ? `- Page: ${source.page_number}` : null,
      `- Source file: ${source.source_url || source.detail?.source_url || "not available"}`,
      `- Stored object: ${source.detail?.asset.status || "not verified"}`,
      `- BM25 rank score: ${formatScore(source.bm25_score)}`,
      `- Inclusion: ${source.inclusionMode === "full" ? "full page text" : "indexed summary only"}`,
      `- Open deterministic checks: ${flags.length}`,
      ...flags.map(flagLine),
    ]
      .filter(Boolean)
      .join("\n");
    return `${header}\n\n${selectedContent(source)}`;
  });

  const lines = coverage(sources);
  const entities = lines.map((line) => line.entity);

  return [
    `# ${sessionLabel || "Analyst context"}`,
    "",
    "## Analyst question",
    prompt,
    "",
    // Mirrors ANSWER SCOPE in the serving engine's analyst payload. Without it
    // a model asked to "compare the banks" will happily discuss banks that are
    // not in the selection.
    "## Answer scope",
    entities.length
      ? [
          `Retrieved scope: ${entities.join(", ")} (${entities.length} ${entities.length === 1 ? "entity" : "entities"}).`,
          "Do not discuss, rank, value, or compare anything outside this scope unless you are explicitly flagging it as missing coverage.",
          "If the question implies a wider set than the scope above, say so rather than filling the gap.",
        ].join("\n")
      : "No sources selected.",
    "",
    // Mirrors CONTEXT COVERAGE. Coverage is a first-class output in the serving
    // engine and the analyst is required to surface insufficiency rather than
    // answer quietly from thin evidence.
    "## Context coverage",
    lines
      .map(
        (line) =>
          `- ${line.entity}: ${line.pages} page${line.pages === 1 ? "" : "s"}; periods ${line.periods.join(", ") || "not stated"}`,
      )
      .join("\n") || "- none",
    "",
    "## Instructions",
    [
      "Answer only from the sources below.",
      "Cite the source number for every material claim.",
      "Separate direct evidence from inference.",
      "Reconcile inconsistencies between sources explicitly, and say when the sources are insufficient.",
      "Treat any listed deterministic check as a known data-quality caveat on that source.",
    ]
      .map((line) => `- ${line}`)
      .join("\n"),
    "",
    "## Human-curated authoritative sources",
    "",
    ...blocks,
  ].join("\n\n");
}

/** The machine-readable handoff, mirroring the markdown for API consumers. */
export function buildContextPayload(options: {
  sessionLabel: string;
  prompt: string;
  provider: string;
  retrieval: AppliedSearch | null;
  sources: StackedSource[];
  markdown: string;
}) {
  const { sessionLabel, prompt, provider, retrieval, sources, markdown } = options;
  return {
    schema_version: CONTEXT_SCHEMA_VERSION,
    session: sessionLabel,
    prompt,
    provider,
    retrieval,
    sources: sources.map((source, index) => ({
      source_number: index + 1,
      id: source.id,
      corpus: source.corpus,
      entity: source.entity,
      title: source.title,
      period: source.period,
      page_number: source.page_number,
      source_url: source.source_url || source.detail?.source_url || null,
      asset_key: source.asset_key,
      asset_status: source.detail?.asset.status || "not verified",
      bm25_score: source.bm25_score,
      deterministic_flags: source.detail?.quality_flags || source.quality_flags,
      inclusion_mode: source.inclusionMode,
      indexed_summary: source.summary,
      content: selectedContent(source),
    })),
    analyst_context_markdown: markdown,
  };
}
