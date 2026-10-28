/**
 * Shape of the read-only corpus API (apps/corpus-browser/api/worker.ts).
 *
 * This module is imported by both the client page and the server routes, so it
 * stays free of secrets and of any server-only import.
 */

export type CorpusKind = "filing" | "announcement" | "macro";

/**
 * One entry per searchable corpus. The worker accepts exactly these ids in its
 * `corpora` parameter, so adding a corpus here without adding it there produces
 * an empty scope rather than an error.
 *
 * Authoritative-source pages (pipeline/01_authoritative_download.py) are not
 * listed yet: the collector writes PDFs and a manifest, but nothing parses them
 * into D1 or an FTS index, so there is nothing to search. When that chain lands,
 * add the id here and to `parseSearchFilters` in the worker.
 */
export const CORPORA: Array<{
  id: CorpusKind;
  label: string;
  short: string;
  note: string;
}> = [
  {
    id: "filing",
    label: "Company filings",
    short: "Filings",
    note: "Annual and quarterly report pages, one row per page",
  },
  {
    id: "announcement",
    label: "PSX announcements",
    short: "Notices",
    note: "Exchange notices, one row per announcement",
  },
  {
    id: "macro",
    label: "Macro reports",
    short: "Macro",
    note: "Economic survey and state-of-the-economy pages",
  },
];

export const CORPUS_LABELS: Record<CorpusKind, string> = Object.fromEntries(
  CORPORA.map((corpus) => [corpus.id, corpus.label]),
) as Record<CorpusKind, string>;

export interface QualityFlag {
  flag_id: string;
  ticker?: string;
  severity: string;
  status: string;
  category?: string | null;
  affected_table?: string | null;
  filing_period?: string | null;
  period_end?: string | null;
  metric?: string | null;
  rule?: string | null;
  message?: string | null;
  generated_at?: string | null;
}

export interface SearchResult {
  id: number;
  corpus: CorpusKind;
  entity: string;
  source_name: string;
  title: string;
  period: string;
  page_number: number | null;
  /** The pipeline's indexed page summary (`search_text`), not a model summary. */
  summary: string;
  /** BM25 match context, with hits delimited by ⟦ ⟧. */
  snippet: string;
  bm25_score: number;
  source_url: string | null;
  thumbnail_url: string | null;
  asset_key: string | null;
  tags: string[];
  quality_flags: QualityFlag[];
}

export interface SearchResponse {
  query: string;
  parsed_terms: string[];
  match_mode: "all" | "any";
  ranking: string;
  result_count: number;
  limit_per_scope: number;
  filters: {
    tickers: string[];
    date_from: string | null;
    date_to: string | null;
    corpora: CorpusKind[];
  };
  results: SearchResult[];
  error?: string;
}

export interface DocumentDetail {
  id: number;
  corpus: CorpusKind;
  /** Full page markdown. This is what "full page" inclusion sends. */
  text: string;
  search_text: string;
  /** The full D1 row. Carries ticker, filing_type, filing_period, pg. */
  metadata?: Record<string, unknown>;
  source_url: string | null;
  thumbnail_url: string | null;
  asset: {
    key: string | null;
    /** `available` means the object was found in R2 by a live head request. */
    status: "available" | "missing" | "external";
    bytes: number | null;
    etag: string | null;
    uploaded_at: string | null;
  };
  quality_flags: QualityFlag[];
  error?: string;
}

export interface StatsResponse {
  corpora: Array<{ corpus: CorpusKind; rows: number }>;
  total_documents: number;
  ranking: string;
  source_assets: string;
}

export type InclusionMode = "summary" | "full";

export interface StackedSource extends SearchResult {
  loading: boolean;
  detail?: DocumentDetail;
  inclusionMode: InclusionMode;
}

export function sourceKey(source: Pick<SearchResult, "corpus" | "id">): string {
  return `${source.corpus}:${source.id}`;
}

export function groupByEntity<T extends { entity: string }>(items: T[]): Array<[string, T[]]> {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const existing = groups.get(item.entity);
    if (existing) existing.push(item);
    else groups.set(item.entity, [item]);
  }
  return [...groups.entries()];
}

/**
 * Splits worker snippet text on its ⟦ ⟧ hit delimiters. Returning parts rather
 * than HTML keeps corpus text out of `dangerouslySetInnerHTML`.
 */
export function highlightParts(text: string): Array<{ text: string; marked: boolean }> {
  if (!text) return [];
  const parts: Array<{ text: string; marked: boolean }> = [];
  let remaining = text;
  while (remaining) {
    const open = remaining.indexOf("⟦");
    if (open < 0) {
      parts.push({ text: remaining, marked: false });
      break;
    }
    if (open > 0) parts.push({ text: remaining.slice(0, open), marked: false });
    const close = remaining.indexOf("⟧", open + 1);
    if (close < 0) {
      parts.push({ text: remaining.slice(open + 1), marked: true });
      break;
    }
    parts.push({ text: remaining.slice(open + 1, close), marked: true });
    remaining = remaining.slice(close + 1);
  }
  return parts;
}

export { compactNumber, formatScore } from "./format";

const SEVERITY_RANK: Record<string, number> = { blocker: 0, error: 1, warning: 2 };

export function worstSeverity(flags: QualityFlag[]): string | null {
  let worst: string | null = null;
  for (const flag of flags) {
    const severity = (flag.severity || "").toLowerCase();
    if (worst === null || (SEVERITY_RANK[severity] ?? 3) < (SEVERITY_RANK[worst] ?? 3)) {
      worst = severity;
    }
  }
  return worst;
}
