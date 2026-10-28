import TICKERS_JSON from "../data/tickers100.json";
import {
  buildTableEvidenceTitle,
  buildPhraseQuery,
  buildSimilarPrototype,
  deriveMatchedHeading,
  deriveSummaryTitle,
  extractPageSignals,
  lexicalTokens,
  meaningfulTokens,
  selectEvidenceTitle,
  type PageSignals,
} from "./retrieval-signals";
import {
  buildCatalogFacets,
  canonicalIndustry,
  CATALOG_SCHEMA,
  industryMatches,
  type CatalogAvailability,
  type CatalogFacet,
} from "./catalog";
import { assembleCoverageAwareResults } from "./result-assembly";

type CorpusKind = "filing" | "announcement" | "macro";

interface D1Result<T> {
  results?: T[];
}

interface D1Statement {
  bind(...values: unknown[]): D1Statement;
  all<T>(): Promise<D1Result<T>>;
  first<T>(): Promise<T | null>;
  run(): Promise<unknown>;
}

interface D1DatabaseLike {
  prepare(sql: string): D1Statement;
}

interface R2ObjectLike {
  size: number;
  httpEtag?: string;
  uploaded?: Date;
}

interface R2BucketLike {
  head(key: string): Promise<R2ObjectLike | null>;
}

interface Env {
  DB: D1DatabaseLike;
  KEY_DB?: D1DatabaseLike;
  SOURCE_BUCKET: R2BucketLike;
  SUPABASE_URL?: string;
  SUPABASE_ANON_KEY?: string;
}

interface AuthenticatedUser {
  id: string;
  email?: string;
}

async function authenticatedUser(request: Request, env: Env): Promise<AuthenticatedUser | null> {
  const authorization = request.headers.get("authorization") || "";
  if (!authorization.startsWith("Bearer ") || !env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) return null;
  try {
    const response = await fetch(`${env.SUPABASE_URL.replace(/\/$/, "")}/auth/v1/user`, {
      headers: {
        Authorization: authorization,
        apikey: env.SUPABASE_ANON_KEY,
      },
    });
    if (!response.ok) return null;
    const payload = await response.json() as {
      id?: string;
      email?: string;
      is_anonymous?: boolean;
    };
    if (!payload.id || payload.is_anonymous) return null;
    return { id: payload.id, email: payload.email };
  } catch {
    return null;
  }
}

interface QualityFlag {
  flag_id: string;
  ticker: string;
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

interface SearchFilters {
  tickers: string[];
  requiredTickers: string[];
  industryTerms: string[];
  years: number[];
  dateFrom: string | null;
  dateTo: string | null;
  corpora: CorpusKind[];
  filingCadence: "annual" | "quarterly" | "any";
  latest: boolean;
  preferRecent: boolean;
  latestPeriods: Record<string, string>;
  queryPack: "financial_statements" | null;
  retrievalMode: "baseline" | "structure" | "key_only" | "combined";
  diagnostics?: Record<string, number>;
  laneDiagnostics?: Record<string, Array<{
    corpus: CorpusKind;
    id: number;
    rank: number;
    score: number;
  }>>;
}

type RetrievalLane =
  | "baseline"
  | "key"
  | "summary"
  | "text"
  | "heading"
  | "heading_index"
  | "table_header"
  | "title"
  | "chapter"
  | "summary_phrase"
  | "text_phrase"
  | "heading_phrase"
  | "title_phrase"
  | "chapter_phrase"
  | "indexed_heading";

interface FieldQueries {
  key?: string;
  summary: string;
  text: string;
  heading: string;
}

interface FieldQueryPlan {
  inputs: FieldQueries;
  bm25: FieldQueries;
  exactKey: string;
}

interface SearchResult {
  id: number;
  corpus: CorpusKind;
  entity: string;
  industry?: string | null;
  industry_id?: string | null;
  industry_path?: string | null;
  catalog_item_id?: string;
  document_id?: string;
  document_type?: string;
  publisher?: string;
  availability?: CatalogAvailability;
  source_name: string;
  title: string;
  period: string;
  page_number: number | null;
  summary: string;
  snippet: string;
  bm25_score: number;
  source_url: string | null;
  thumbnail_url: string | null;
  asset_key: string | null;
  tags: string[];
  quality_flags: QualityFlag[];
  result_kind?: string;
  relevance_reason?: string;
  retrieval_basis?: "bm25" | "indexed_heading" | "rank_fusion";
  retrieval_lanes?: RetrievalLane[];
  fusion_score?: number;
  rerank_score?: number;
  evidence_score?: number;
  corpus_support?: number;
  derived_heading?: string;
  table_signature?: string[];
  page_signals?: PageSignals;
  page_index?: {
    schema_version: string;
    display_summary: string;
    parse_status: "ok" | "incomplete" | "reextract";
    metadata_alignment: "aligned" | "unclear" | "conflict";
  };
}

const SOURCE_BASE_URL = "https://source.psxgpt.com";
const RETRIEVAL_VERSION = "ledger-retrieval.v4";
const MAX_QUERY_TOKENS = 16;
const INDUSTRY_BY_TICKER = new Map(
  (TICKERS_JSON as Array<Record<string, unknown>>)
    .map((row) => [
      String(row.Symbol || "").trim().toUpperCase(),
      String(row.Industry || "").trim(),
    ] as const)
    .filter(([ticker]) => ticker),
);

function industryTickers(terms: string[]): string[] {
  if (terms.length === 0) return [];
  return [...INDUSTRY_BY_TICKER.entries()]
    .filter(([, industry]) => industryMatches(industry, terms))
    .map(([ticker]) => ticker);
}
const MAX_RESULTS_PER_SCOPE = 12;
const MAX_TICKERS = 12;
const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Max-Age": "86400",
};

function json(data: unknown, status = 200, cacheControl?: string): Response {
  return Response.json(data, {
    status,
    headers: {
      ...CORS_HEADERS,
      "Cache-Control": cacheControl || (status === 200 ? "public, max-age=30" : "no-store"),
    },
  });
}

function normalizeText(value: unknown, maxChars = 800): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > maxChars ? `${text.slice(0, maxChars - 1)}…` : text;
}

function parseTags(value: unknown): string[] {
  const normalizeTags = (items: unknown[]): string[] => items
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (item && typeof item === "object" && "tag" in item) {
        return String((item as { tag?: unknown }).tag || "").trim();
      }
      return "";
    })
    .filter(Boolean)
    .slice(0, 6);
  if (Array.isArray(value)) return normalizeTags(value);
  if (typeof value !== "string" || !value.trim()) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? normalizeTags(parsed) : [];
  } catch {
    return value.split(",").map((part) => part.trim()).filter(Boolean).slice(0, 6);
  }
}

function parseSearchFilters(url: URL): SearchFilters {
  const requestedRetrievalMode = url.searchParams.get("retrieval_mode");
  const retrievalMode = requestedRetrievalMode === "baseline"
    ? "baseline"
    : requestedRetrievalMode === "key_only"
      ? "key_only"
      : requestedRetrievalMode === "combined"
        ? "combined"
        : "structure";
  const tickers = [...new Set(
    (url.searchParams.get("tickers") || "")
      .split(",")
      .map((ticker) => ticker.trim().toUpperCase())
      .filter((ticker) => /^[A-Z0-9.-]{1,16}$/.test(ticker)),
  )].slice(0, MAX_TICKERS);
  const tickerSet = new Set(tickers);
  const requestedRequiredTickers = [...new Set(
    (url.searchParams.get("required_tickers") || "")
      .split(",")
      .map((ticker) => ticker.trim().toUpperCase())
      .filter((ticker) => tickerSet.has(ticker)),
  )];

  const dateValue = (name: string): string | null => {
    const value = (url.searchParams.get(name) || "").trim();
    return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
  };

  const requestedCorpora = (url.searchParams.get("corpora") || "filing,announcement,macro")
    .split(",")
    .map((value) => value.trim())
    .filter((value): value is CorpusKind => ["filing", "announcement", "macro"].includes(value));

  const years = [...new Set(
    (url.searchParams.get("years") || "")
      .split(",")
      .map(Number)
      .filter((year) => Number.isInteger(year) && year >= 1900 && year <= 2100),
  )].slice(0, 10);

  return {
    tickers,
    requiredTickers: requestedRequiredTickers.length
      ? requestedRequiredTickers
      : tickers,
    industryTerms: [...new Set(
      (url.searchParams.get("industries") || "")
        .split(",")
        .map((value) => value.trim().toLowerCase())
        .filter((value) => /^[a-z &./-]{2,40}$/.test(value)),
    )].slice(0, 4),
    years,
    dateFrom: dateValue("date_from"),
    dateTo: dateValue("date_to"),
    corpora: [...new Set(requestedCorpora)].length
      ? [...new Set(requestedCorpora)]
      : ["filing"],
    filingCadence: url.searchParams.get("filing_cadence") === "annual"
      ? "annual"
      : url.searchParams.get("filing_cadence") === "quarterly"
        ? "quarterly"
        : "any",
    latest: url.searchParams.get("latest") === "1",
    preferRecent: url.searchParams.get("prefer_recent") === "1",
    latestPeriods: {},
    // Statement packs are disabled while the general field-retrieval path is
    // evaluated. Ignore legacy query_pack parameters instead of allowing a
    // caller to bypass the searchable evidence path.
    queryPack: null,
    retrievalMode,
    diagnostics: url.searchParams.get("debug") === "1" ? {} : undefined,
    laneDiagnostics: url.searchParams.get("debug") === "1" ? {} : undefined,
  };
}

export function buildFtsQueries(input: string): { phrase: string; all: string; any: string; tokens: string[] } {
  const tokens = meaningfulTokens(input).slice(0, MAX_QUERY_TOKENS);
  const quoted = tokens.map((token) => `"${token}"`);
  return {
    tokens,
    phrase: buildPhraseQuery(input),
    all: quoted.join(" AND "),
    any: quoted.join(" OR "),
  };
}

function fieldMatch(column: string, query: string): string {
  return `${column} : (${query})`;
}

function laneLabel(lane: RetrievalLane): string {
  if (lane === "baseline") return "existing field retrieval";
  if (lane === "summary_phrase") return "exact phrase in indexed summary";
  if (lane === "text_phrase") return "exact phrase in page text";
  if (lane === "heading_phrase") return "exact phrase in page heading";
  if (lane === "title_phrase") return "exact phrase in announcement title";
  if (lane === "chapter_phrase") return "exact phrase in report chapter";
  if (lane === "summary") return "indexed summary";
  if (lane === "text") return "page text";
  if (lane === "heading") return "heading query in indexed summary";
  if (lane === "heading_index") return "Markdown heading index";
  if (lane === "table_header") return "table header index";
  if (lane === "title") return "announcement title";
  if (lane === "chapter") return "report chapter";
  if (lane === "indexed_heading") return "all-term indexed summary";
  return "exact searchable key";
}

function laneWeight(lane: RetrievalLane): number {
  // The generated Key is a recall signal. The established field ranker has
  // already combined summary, text, heading, phrase, and page structure, so a
  // single Key lane must not receive an equal second vote over that evidence.
  if (lane === "baseline") return 1.25;
  if (lane === "key") return 0.7;
  if (lane.endsWith("_phrase")) return 1.85;
  if (lane === "indexed_heading") return 1.6;
  if (lane === "heading_index") return 1.35;
  if (lane === "table_header") return 1.15;
  if (
    lane === "heading"
    || lane === "title"
    || lane === "chapter"
  ) return 1.2;
  if (lane === "summary") return 1.1;
  return 1;
}

/**
 * Reciprocal-rank fusion combines independent field rankings without pretending
 * that BM25 scores from short summaries, long pages, and titles share a scale.
 */
export function fuseRankedLanes(
  lanes: Array<{ lane: RetrievalLane; results: SearchResult[] }>,
  limit: number,
): SearchResult[] {
  const fused = new Map<string, {
    result: SearchResult;
    score: number;
    lanes: Set<RetrievalLane>;
    bestRank: number;
  }>();
  const RRF_K = 60;

  for (const { lane, results } of lanes) {
    results.forEach((result, index) => {
      const key = `${result.corpus}:${result.id}`;
      const rank = index + 1;
      const existing = fused.get(key);
      if (existing) {
        existing.score += laneWeight(lane) / (RRF_K + rank);
        existing.lanes.add(lane);
        existing.bestRank = Math.min(existing.bestRank, rank);
        if (result.page_index && !existing.result.page_index) {
          existing.result.page_index = result.page_index;
        }
        if (lane === "text" && result.snippet) {
          existing.result.snippet = result.snippet;
          if (existing.result.corpus === "macro") existing.result.summary = result.summary;
        }
        existing.result.bm25_score = Math.max(existing.result.bm25_score, result.bm25_score);
      } else {
        fused.set(key, {
          result: { ...result },
          score: laneWeight(lane) / (RRF_K + rank),
          lanes: new Set([lane]),
          bestRank: rank,
        });
      }
    });
  }

  return [...fused.values()]
    .sort((left, right) =>
      right.score - left.score
      || left.bestRank - right.bestRank
      || (left.result.page_number ?? 0) - (right.result.page_number ?? 0),
    )
    .slice(0, limit)
    .map(({ result, score, lanes }) => ({
      ...result,
      retrieval_basis: "rank_fusion",
      retrieval_lanes: [...lanes],
      fusion_score: Number(score.toFixed(6)),
      relevance_reason: `Matched ${[...lanes].map(laneLabel).join(" + ")}`,
    }));
}

function recordLaneCandidates(
  filters: SearchFilters,
  corpus: CorpusKind,
  lane: RetrievalLane,
  results: SearchResult[],
) {
  if (!filters.laneDiagnostics) return;
  filters.laneDiagnostics[`${corpus}.${lane}`] = results.map((result, index) => ({
    corpus,
    id: result.id,
    rank: index + 1,
    score: result.bm25_score,
  }));
}

async function queryRows<T>(
  db: D1DatabaseLike,
  sql: string,
  bindings: unknown[],
): Promise<T[]> {
  const result = await db.prepare(sql).bind(...bindings).all<T>();
  return result.results || [];
}

function dateClauses(
  filters: SearchFilters,
  column: string,
  clauses: string[],
  bindings: unknown[],
) {
  if (filters.dateFrom) {
    clauses.push(`${column} >= ?`);
    bindings.push(filters.dateFrom);
  }
  if (filters.dateTo) {
    clauses.push(`${column} <= ?`);
    bindings.push(filters.dateTo);
  }
}

function isNavigationOrCover(result: SearchResult): boolean {
  return (result.page_signals?.navigationLikelihood || 0) >= 0.65;
}

function navigationAwareLimit(results: SearchResult[], limit: number): SearchResult[] {
  const useful = results.filter((result) => !isNavigationOrCover(result));
  if (useful.length >= limit) return useful.slice(0, limit);
  const rejected = results.filter(isNavigationOrCover);
  return [...useful, ...rejected].slice(0, limit);
}

function entityBalancedLimit(
  results: SearchResult[],
  limit: number,
  perEntity = 2,
  fillRemaining = true,
): SearchResult[] {
  const chosen: SearchResult[] = [];
  const counts = new Map<string, number>();
  for (const result of results) {
    const count = counts.get(result.entity) || 0;
    if (count >= perEntity) continue;
    counts.set(result.entity, count + 1);
    chosen.push(result);
    if (chosen.length >= limit) break;
  }
  if (chosen.length >= limit || !fillRemaining) return chosen;
  for (const result of results) {
    if (chosen.some((item) => item.corpus === result.corpus && item.id === result.id)) continue;
    chosen.push(result);
    if (chosen.length >= limit) break;
  }
  return chosen;
}

function yearBalancedLimit(results: SearchResult[], years: number[], perYear: number): SearchResult[] {
  const chosen: SearchResult[] = [];
  const seen = new Set<string>();
  for (const year of [...years].sort((left, right) => right - left)) {
    for (const result of results.filter((item) => item.period.startsWith(String(year))).slice(0, perYear)) {
      const key = `${result.corpus}:${result.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      chosen.push(result);
    }
  }
  return chosen;
}

function normalizedPhrase(value: string): string {
  return lexicalTokens(value, 24).join(" ");
}

function tokenCoverage(needle: string, haystack: string): number {
  const tokens = [...new Set(lexicalTokens(needle, 20).filter((token) => token.length >= 3))];
  if (!tokens.length) return 0;
  const haystackTokens = new Set(lexicalTokens(haystack, 500));
  const matches = (token: string): boolean => {
    if (haystackTokens.has(token)) return true;
    if (token.length < 4) return false;
    return [...haystackTokens].some((candidate) =>
      candidate.length >= 4
      && Math.abs(candidate.length - token.length) <= 3
      && (candidate.startsWith(token) || token.startsWith(candidate)),
    );
  };
  return tokens.filter(matches).length / tokens.length;
}

function globalSubjectGuard(results: SearchResult[], inputs: FieldQueries, limit: number): SearchResult[] {
  const headingTokens = new Set(lexicalTokens(inputs.heading, 20));
  const generic = new Set([
    "annual", "company", "companies", "financial", "information", "latest", "page",
    "report", "reports", "statement", "statements", "year", "years",
  ]);
  const summarySubjects = [...new Set(lexicalTokens(inputs.summary, 20))]
    .filter((token) => token.length >= 3 && !headingTokens.has(token) && !generic.has(token));
  const textSubjects = [...new Set(lexicalTokens(inputs.text, 20))]
    .filter((token) => token.length >= 3 && !headingTokens.has(token) && !generic.has(token));
  const subjectTokens = summarySubjects.length ? summarySubjects : textSubjects;
  if (!subjectTokens.length) return results;
  const guarded = results.filter((result) => {
    const evidence = normalizedPhrase([
      result.title,
      result.summary,
      result.snippet,
      ...(result.table_signature || []),
    ].join(" "));
    return subjectTokens.some((token) => evidence.includes(token));
  });
  return guarded.length >= Math.min(limit, 3) ? guarded : [...guarded, ...results.filter((result) => !guarded.includes(result))];
}

function rerankFilingCandidates(
  results: SearchResult[],
  fieldInputs: FieldQueries,
  limit: number,
): SearchResult[] {
  const headingPhrase = normalizedPhrase(fieldInputs.heading);
  const textPhrase = normalizedPhrase(fieldInputs.text);
  return results
    .map((result) => {
      const heading = result.derived_heading || "";
      const table = (result.table_signature || []).join(" ");
      const normalizedHeading = normalizedPhrase(heading);
      const normalizedSummary = normalizedPhrase(result.summary);
      const exactHeading = headingPhrase.length >= 3 && normalizedHeading.includes(headingPhrase);
      const exactSummary = textPhrase.length >= 3 && normalizedSummary.includes(textPhrase);
      const headingCoverage = tokenCoverage(fieldInputs.heading, heading);
      const tableCoverage = tokenCoverage(`${fieldInputs.heading} ${fieldInputs.text}`, table);
      const laneAgreement = new Set(
        (result.retrieval_lanes || []).map((lane) => lane.replace(/_phrase$/, "")),
      ).size;
      const structuredEvidence =
        (result.page_signals?.tableRows || 0) >= 3
        || (result.page_signals?.numericDensity || 0) >= 0.12;
      const rerankScore = (result.fusion_score || 0)
        + (exactHeading ? 0.09 : 0)
        + (exactSummary ? 0.035 : 0)
        + headingCoverage * 0.05
        + tableCoverage * 0.025
        + Math.max(0, laneAgreement - 1) * 0.008
        + (structuredEvidence ? 0.018 : 0)
        - (isNavigationOrCover(result) ? 0.14 : 0);
      return { ...result, rerank_score: Number(rerankScore.toFixed(6)) };
    })
    .sort((left, right) =>
      (right.rerank_score || 0) - (left.rerank_score || 0)
      || (right.fusion_score || 0) - (left.fusion_score || 0)
      || (left.page_number || 0) - (right.page_number || 0),
    )
    .slice(0, limit);
}

function pageIndexMetadata(row: Record<string, unknown>): SearchResult["page_index"] | undefined {
  const displaySummary = normalizeText(row.display_summary, 1_200);
  if (!displaySummary) return undefined;
  const parseStatus = ["ok", "incomplete", "reextract"].includes(String(row.parse_status))
    ? String(row.parse_status) as "ok" | "incomplete" | "reextract"
    : "ok";
  const metadataAlignment = ["aligned", "unclear", "conflict"].includes(String(row.metadata_alignment))
    ? String(row.metadata_alignment) as "aligned" | "unclear" | "conflict"
    : "aligned";
  return {
    schema_version: String(row.schema_version || "page-index.v3"),
    display_summary: displaySummary,
    parse_status: parseStatus,
    metadata_alignment: metadataAlignment,
  };
}

function filingResultFromRow(
  row: Record<string, unknown>,
  queries: FieldQueryPlan,
): SearchResult {
  const rowTicker = String(row.ticker || "").toUpperCase();
  const page = Number(row.pg) || 0;
  const filingType = String(row.filing_type || "filing");
  const jpgPath = String(row.jpg_path || "");
  const industry = String(row.industry || INDUSTRY_BY_TICKER.get(rowTicker) || "");
  const canonical = canonicalIndustry(industry);
  const documentPath = jpgPath.split("/").slice(0, -1).join("/");
  const tags = parseTags(row.section_tags);
  const summaryTitle = deriveSummaryTitle(row.search_text, queries.inputs.text);
  const fallbackTitle = `${filingType[0]?.toUpperCase() || ""}${filingType.slice(1)} report — page ${page}`;
  return {
    id: Number(row.id),
    corpus: "filing",
    entity: rowTicker,
    industry: canonical?.name || industry || null,
    industry_id: canonical?.id || null,
    industry_path: canonical?.path || null,
    catalog_item_id: `filing:${Number(row.id)}`,
    document_id: documentPath ? `filing:${documentPath}` : `filing:${rowTicker}:${filingType}:${String(row.filing_period || "")}`,
    document_type: filingType,
    publisher: rowTicker,
    availability: { indexed: true, parsed: true, r2_linked: Boolean(jpgPath) },
    source_name: rowTicker,
    title: summaryTitle || fallbackTitle,
    period: String(row.filing_period || ""),
    page_number: page,
    summary: normalizeText(row.search_text),
    snippet: normalizeText(row.snippet || row.display_summary, 500),
    bm25_score: Math.abs(Number(row.score) || 0),
    source_url: jpgPath ? `${SOURCE_BASE_URL}/PDF_PAGES/${jpgPath.replace(/\.jpg$/i, ".pdf")}` : null,
    thumbnail_url: jpgPath ? `${SOURCE_BASE_URL}/PDF_THUMBNAILS/${jpgPath}` : null,
    asset_key: jpgPath ? `PDF_PAGES/${jpgPath.replace(/\.jpg$/i, ".pdf")}` : null,
    tags,
    quality_flags: [],
    derived_heading: summaryTitle || undefined,
    table_signature: [],
    page_index: pageIndexMetadata(row),
  };
}

function announcementResultFromRow(row: Record<string, unknown>): SearchResult {
  const rowTicker = String(row.ticker || "").toUpperCase();
  const rawIndustry = INDUSTRY_BY_TICKER.get(rowTicker) || "";
  const canonical = canonicalIndustry(rawIndustry);
  const localPath = String(row.local_path || "").replace(/^announcement_documents\//, "");
  const isPdf = /\.pdf$/i.test(localPath) || /pdf/i.test(String(row.content_type || ""));
  const sourceUrl = localPath
    ? `${SOURCE_BASE_URL}/ANNOUNCEMENTS/${localPath}`
    : String(row.direct_url || row.source_url || "") || null;
  return {
    id: Number(row.id),
    corpus: "announcement",
    entity: rowTicker,
    industry: canonical?.name || rawIndustry || null,
    industry_id: canonical?.id || null,
    industry_path: canonical?.path || null,
    catalog_item_id: `announcement:${Number(row.id)}`,
    document_id: `announcement:${Number(row.id)}`,
    document_type: "announcement",
    publisher: "Pakistan Stock Exchange",
    availability: { indexed: true, parsed: true, r2_linked: Boolean(localPath) },
    source_name: String(row.company_name || rowTicker),
    title: String(row.title || "PSX announcement"),
    period: [row.announcement_date, row.announcement_time].filter(Boolean).join(" "),
    page_number: null,
    summary: normalizeText(row.search_text),
    snippet: normalizeText(row.snippet || row.display_summary, 500),
    bm25_score: Math.abs(Number(row.score) || 0),
    source_url: sourceUrl,
    thumbnail_url: localPath
      ? isPdf
        ? `${SOURCE_BASE_URL}/ANNOUNCEMENT_THUMBNAILS/${localPath.replace(/\.pdf$/i, ".jpg")}`
        : sourceUrl
      : null,
    asset_key: localPath ? `ANNOUNCEMENTS/${localPath}` : null,
    tags: [String(row.notice_status || "")].filter(Boolean),
    quality_flags: [],
    page_index: pageIndexMetadata(row),
  };
}

function macroResultFromRow(row: Record<string, unknown>): SearchResult {
  const sourceDoc = String(row.source_doc || "");
  const chapter = String(row.chapter || "");
  const page = Number(row.page_num) || 0;
  const pageFile = `page_${String(page).padStart(3, "0")}`;
  const rel = [sourceDoc, chapter, pageFile].filter(Boolean).join("/");
  const docType = String(row.doc_type || "macro").replace(/_/g, " ");
  return {
    id: Number(row.id),
    corpus: "macro",
    entity: docType.toUpperCase(),
    industry: null,
    industry_id: null,
    industry_path: null,
    catalog_item_id: `macro:${Number(row.id)}`,
    document_id: `macro:${sourceDoc}:${String(row.fiscal_year || "")}`,
    document_type: String(row.doc_type || "macro"),
    publisher: docType,
    availability: { indexed: true, parsed: true, r2_linked: Boolean(rel) },
    source_name: docType,
    title: chapter || `${docType} — page ${page}`,
    period: String(row.fiscal_year || ""),
    page_number: page,
    summary: normalizeText(row.display_summary || row.snippet),
    snippet: normalizeText(row.snippet || row.display_summary, 500),
    bm25_score: Math.abs(Number(row.score) || 0),
    source_url: rel ? `${SOURCE_BASE_URL}/MACRO_PAGES/${rel}.pdf` : null,
    thumbnail_url: rel ? `${SOURCE_BASE_URL}/MACRO_THUMBNAILS/${rel}.jpg` : null,
    asset_key: rel ? `MACRO_PAGES/${rel}.pdf` : null,
    tags: [String(row.content_type || "")].filter(Boolean),
    quality_flags: [],
    page_index: pageIndexMetadata(row),
  };
}

interface StructureCandidate {
  source_path: string;
  headings: string;
  table_headers: string;
  score: number;
}

async function searchFilingStructureLane(
  structureDb: D1DatabaseLike,
  db: D1DatabaseLike,
  column: "headings" | "table_headers",
  query: string,
  queries: FieldQueryPlan,
  limit: number,
  filters: SearchFilters,
  ticker?: string,
  year?: number,
): Promise<SearchResult[]> {
  if (!query) return [];
  const clauses = ["page_structures_fts MATCH ?"];
  const bindings: unknown[] = [fieldMatch(column, query)];
  if (ticker) {
    clauses.push("s.entity = ?");
    bindings.push(ticker);
  } else if (filters.tickers.length > 0) {
    clauses.push(`s.entity IN (${filters.tickers.map(() => "?").join(", ")})`);
    bindings.push(...filters.tickers);
  } else if (filters.industryTerms.length > 0) {
    const scopedTickers = industryTickers(filters.industryTerms);
    if (scopedTickers.length === 0) return [];
    clauses.push(`s.entity IN (${scopedTickers.map(() => "?").join(", ")})`);
    bindings.push(...scopedTickers);
  }
  if (year) {
    clauses.push("substr(s.period, 1, 4) = ?");
    bindings.push(String(year));
  } else if (filters.years.length > 0) {
    clauses.push(`substr(s.period, 1, 4) IN (${filters.years.map(() => "?").join(", ")})`);
    bindings.push(...filters.years.map(String));
  } else {
    dateClauses(filters, "s.period", clauses, bindings);
  }
  if (filters.filingCadence !== "any") {
    clauses.push("s.document_type = ?");
    bindings.push(filters.filingCadence);
  }
  if (filters.latest) {
    if (ticker && filters.latestPeriods[ticker]) {
      clauses.push("s.period = ?");
      bindings.push(filters.latestPeriods[ticker]);
    } else {
      const latestScopes = Object.entries(filters.latestPeriods);
      if (latestScopes.length > 0) {
        clauses.push(`(${latestScopes.map(() => "(s.entity = ? AND s.period = ?)").join(" OR ")})`);
        for (const [scopeTicker, period] of latestScopes) bindings.push(scopeTicker, period);
      }
    }
  }
  const candidateLimit = Math.min(72, Math.max(18, limit * 4));
  bindings.push(candidateLimit);
  const weights = column === "headings" ? "1.0, 0.0" : "0.0, 1.0";
  const candidates = await queryRows<StructureCandidate>(
    structureDb,
    `SELECT
       s.source_path,
       s.headings,
       s.table_headers,
       bm25(page_structures_fts, ${weights}) AS score
     FROM page_structures_fts
     JOIN page_structures s ON s.id = page_structures_fts.rowid
     WHERE ${clauses.join(" AND ")}
     ORDER BY score
     LIMIT ?`,
    bindings,
  );
  if (candidates.length === 0) return [];

  const paths = candidates.map((candidate) => candidate.source_path);
  const rows = await queryRows<Record<string, unknown>>(
    db,
    `SELECT id, ticker, industry, filing_type, filing_period, pg, jpg_path,
            section_tags, search_text
     FROM financial_documents_new
     WHERE jpg_path IN (${paths.map(() => "?").join(", ")})`,
    paths,
  );
  const rowsByPath = new Map(rows.map((row) => [String(row.jpg_path), row]));
  return candidates.flatMap((candidate) => {
    const row = rowsByPath.get(candidate.source_path);
    if (!row) return [];
    const structuralText = column === "headings"
      ? candidate.headings
      : candidate.table_headers;
    return [filingResultFromRow({
      ...row,
      score: candidate.score,
      snippet: structuralText,
    }, queries)];
  });
}

async function searchFilingsForTicker(
  db: D1DatabaseLike,
  structureDb: D1DatabaseLike | undefined,
  queries: FieldQueryPlan,
  limit: number,
  filters: SearchFilters,
  ticker?: string,
  year?: number,
): Promise<SearchResult[]> {
  const filingStarted = performance.now();
  const candidateLimit = ticker
    ? Math.min(36, Math.max(24, limit * 3))
    : filters.tickers.length > 1
      ? Math.min(96, Math.max(32, limit * 6))
      : Math.min(48, Math.max(18, limit * 5));
  const runLane = async (
    column: "search_text" | "text",
    query: string,
  ): Promise<Record<string, unknown>[]> => {
    if (!query) return [];
    const clauses = ["filings_fts_new MATCH ?"];
    const bindings: unknown[] = [fieldMatch(column, query)];
    if (ticker) {
      clauses.push("d.ticker = ?");
      bindings.push(ticker);
    } else if (filters.tickers.length > 0) {
      clauses.push(`d.ticker IN (${filters.tickers.map(() => "?").join(", ")})`);
      bindings.push(...filters.tickers);
    } else if (filters.industryTerms.length > 0) {
      const scopedTickers = industryTickers(filters.industryTerms);
      if (scopedTickers.length === 0) return [];
      clauses.push(`d.ticker IN (${scopedTickers.map(() => "?").join(", ")})`);
      bindings.push(...scopedTickers);
    }
    if (year) {
      clauses.push("substr(d.filing_period, 1, 4) = ?");
      bindings.push(String(year));
    } else if (filters.years.length > 0) {
      clauses.push(`substr(d.filing_period, 1, 4) IN (${filters.years.map(() => "?").join(", ")})`);
      bindings.push(...filters.years.map(String));
    }
    if (filters.filingCadence !== "any") {
      clauses.push("d.filing_type = ?");
      bindings.push(filters.filingCadence);
    }
    if (filters.latest) {
      if (ticker && filters.latestPeriods[ticker]) {
        clauses.push("d.filing_period = ?");
        bindings.push(filters.latestPeriods[ticker]);
      } else {
        const latestScopes = Object.entries(filters.latestPeriods);
        if (latestScopes.length > 0) {
          clauses.push(`(${latestScopes.map(() => "(d.ticker = ? AND d.filing_period = ?)").join(" OR ")})`);
          for (const [scopeTicker, period] of latestScopes) bindings.push(scopeTicker, period);
        }
      }
    }
    // A concrete year set already constrains the filing period. Adding the
    // equivalent range forces extra work in every FTS lane and can push
    // multi-year research requests over the Worker latency budget.
    if (!year && filters.years.length === 0) {
      dateClauses(filters, "d.filing_period", clauses, bindings);
    }
    bindings.push(candidateLimit);

    const snippetColumn = column === "search_text" ? 0 : 1;
    const weights = column === "search_text" ? "1.0, 0.0" : "0.0, 1.0";
    const rows = await queryRows<Record<string, unknown>>(
      db,
      `SELECT
         d.id,
         d.ticker,
         d.industry,
         d.filing_type,
         d.filing_period,
         d.pg,
         d.jpg_path,
         d.section_tags,
         d.search_text,
         snippet(filings_fts_new, ${snippetColumn}, '⟦', '⟧', ' … ', 24) AS snippet,
         bm25(filings_fts_new, ${weights}) AS score
       FROM filings_fts_new
       JOIN financial_documents_new d ON d.id = filings_fts_new.rowid
       WHERE ${clauses.join(" AND ")}
       ORDER BY score
       LIMIT ?`,
      bindings,
    );
    return rows;
  };

  type FilingLaneSpec = {
    lane: RetrievalLane;
    column: "search_text" | "text";
    query: string;
  };
  const coreSpecs: FilingLaneSpec[] = [
    { lane: "indexed_heading", column: "search_text", query: queries.exactKey },
    { lane: "summary", column: "search_text", query: queries.bm25.summary },
    { lane: "text", column: "text", query: queries.bm25.text },
    { lane: "heading", column: "search_text", query: queries.bm25.heading },
  ];
  const lanesStarted = performance.now();
  const structureStarted = performance.now();
  const [laneOutputs, structureResults] = await Promise.all([
    Promise.all(coreSpecs.map(async (spec) => {
      const started = performance.now();
      const rows = await runLane(spec.column, spec.query);
      if (filters.diagnostics) {
        filters.diagnostics[`filing_${spec.lane}_lane_ms`] = Number((performance.now() - started).toFixed(2));
      }
      return { spec, rows };
    })),
    filters.retrievalMode === "structure" && structureDb
      ? Promise.all([
          searchFilingStructureLane(
            structureDb,
            db,
            "headings",
            buildFtsQueries(queries.inputs.heading).all,
            queries,
            limit,
            filters,
            ticker,
            year,
          ),
          searchFilingStructureLane(
            structureDb,
            db,
            "table_headers",
            buildFtsQueries(queries.inputs.text).all,
            queries,
            limit,
            filters,
            ticker,
            year,
          ),
        ])
      : Promise.resolve([]),
  ]);
  if (filters.diagnostics) {
    filters.diagnostics.filing_lanes_ms = Number((performance.now() - lanesStarted).toFixed(2));
    if (structureResults.length > 0) {
      filters.diagnostics.filing_structure_lanes_ms = Number((performance.now() - structureStarted).toFixed(2));
    }
  }

  const mapRows = (rows: Record<string, unknown>[]): SearchResult[] =>
    rows.map((row) => filingResultFromRow(row, queries));

  const mappedLanes = laneOutputs.map(({ spec, rows }) => ({
    lane: spec.lane,
    results: mapRows(rows),
  }));
  if (structureResults.length === 2) {
    const structureOutputs = [
      {
        lane: "heading_index" as const,
        results: structureResults[0],
      },
      {
        lane: "table_header" as const,
        results: structureResults[1],
      },
    ];
    mappedLanes.push(...structureOutputs);
  }
  for (const lane of mappedLanes) {
    recordLaneCandidates(filters, "filing", lane.lane, lane.results);
  }
  const fused = !ticker && filters.tickers.length > 1
    ? filters.tickers.flatMap((entity) =>
        fuseRankedLanes(
          mappedLanes.map((lane) => ({
            ...lane,
            results: lane.results.filter((result) => result.entity === entity),
          })),
          Math.max(12, Math.ceil(limit / filters.tickers.length) * 3),
        )
      )
    : fuseRankedLanes(mappedLanes, limit * 3);
  const structuralCandidates = ticker
    ? fused.slice(0, Math.max(limit * 3, 24))
    : filters.tickers.length > 1
      ? entityBalancedLimit(
          fused,
          Math.min(fused.length, filters.tickers.length * 6),
          6,
          false,
        )
      : fused.slice(0, Math.max(limit * 3, 12));
  const structuralIds = structuralCandidates.map((result) => result.id);
  const signalsById = new Map<number, PageSignals>();
  const contentById = new Map<number, string>();
  if (structuralIds.length > 0) {
    const loadStarted = performance.now();
    const signalRows = await queryRows<{ id: number; content_head: string }>(
      db,
      `SELECT id, substr(text, 1, 6000) AS content_head
       FROM financial_documents_new
       WHERE id IN (${structuralIds.map(() => "?").join(", ")})`,
      structuralIds,
    );
    if (filters.diagnostics) {
      filters.diagnostics.filing_candidate_load_ms = Number((performance.now() - loadStarted).toFixed(2));
      filters.diagnostics.filing_candidate_count = structuralIds.length;
    }
    const parseStarted = performance.now();
    for (const row of signalRows) {
      const content = String(row.content_head || "");
      contentById.set(Number(row.id), content);
      signalsById.set(Number(row.id), extractPageSignals(content));
    }
    if (filters.diagnostics) {
      filters.diagnostics.filing_signal_parse_ms = Number((performance.now() - parseStarted).toFixed(2));
    }
  }
  const enriched = structuralCandidates.map((result) => {
    const signals = signalsById.get(result.id) || extractPageSignals("");
    const content = contentById.get(result.id) || "";
    const matchedHeading = deriveMatchedHeading(content, queries.inputs.text);
    const summaryTitle = deriveSummaryTitle(result.summary, queries.inputs.text);
    const fallbackTitle = result.title;
    const structuralTitle = signals.title === "Filing page" ? summaryTitle : signals.title;
    const tableTitle = buildTableEvidenceTitle(
      signals.tableSignature,
      `${queries.inputs.heading} ${queries.inputs.text}`,
    );
    const evidenceTitle = selectEvidenceTitle(
      [matchedHeading, tableTitle, structuralTitle, summaryTitle, fallbackTitle],
      `${queries.inputs.heading} ${queries.inputs.text}`,
    );
    return {
      ...result,
      title: evidenceTitle || fallbackTitle,
      derived_heading: evidenceTitle || result.derived_heading,
      table_signature: signals.tableSignature,
      page_signals: signals,
    };
  });
  const scopedMultiplier = filters.years.length > 0 ? 5 : 2;
  const reranked = navigationAwareLimit(
    rerankFilingCandidates(
      enriched,
      queries.inputs,
      ticker ? limit * scopedMultiplier : limit * 8,
    ),
    ticker ? limit * scopedMultiplier : limit * 6,
  );
  if (filters.diagnostics) {
    filters.diagnostics.filing_total_ms = Number((performance.now() - filingStarted).toFixed(2));
  }
  if (ticker && filters.years.length > 0) {
    return yearBalancedLimit(
      reranked,
      filters.years,
      Math.max(1, Math.ceil(limit / filters.years.length)),
    );
  }
  if (!ticker && filters.tickers.length > 1) {
    // Preserve a deep cross-entity candidate pool here. The caller applies the
    // final per-company cap after it can see all requested entities.
    return reranked.slice(0, Math.min(reranked.length, limit * 4));
  }
  return ticker
    ? reranked.slice(0, limit)
    : entityBalancedLimit(globalSubjectGuard(reranked, queries.inputs, limit), limit);
}

const FINANCIAL_STATEMENT_PACK = [
  {
    kind: "financial_position",
    label: "Statement of financial position",
    cue: /(?:unconsolidated |consolidated )?statement of financial position\b/i,
  },
  {
    kind: "profit_loss",
    label: "Profit and loss account",
    cue: /(?:unconsolidated |consolidated )?(?:profit and loss account|statement of profit or loss)\b/i,
  },
  {
    kind: "cash_flow",
    label: "Cash flow statement",
    cue: /(?:unconsolidated |consolidated )?(?:cash flow statement|statement of cash flows)\b/i,
  },
  {
    kind: "changes_equity",
    label: "Statement of changes in equity",
    cue: /(?:unconsolidated |consolidated )?statement of changes in equity\b/i,
  },
] as const;

const FINANCIAL_STATEMENT_FTS_QUERY = [
  '"statement of financial position"',
  '"profit and loss account"',
  '"statement of profit or loss"',
  '"cash flow statement"',
  '"statement of cash flows"',
  '"statement of changes in equity"',
].join(" OR ");

interface StatementCandidate {
  result: SearchResult;
  textHead: string;
}

function statementCandidateScore(
  candidate: StatementCandidate,
  component: typeof FINANCIAL_STATEMENT_PACK[number],
): number {
  const summary = candidate.result.summary.replace(/[⟦⟧]/g, "").trim();
  const textHead = candidate.textHead.replace(/[⟦⟧]/g, "").trim();
  const summaryPrefix = summary.slice(0, 260);
  const headingPrefix = textHead.slice(0, 700);
  const hardReject = /\b(contents?|table of contents|page index|section listing|report structure|six years?|horizontal analysis|islamic banking business|annexure)\b/i;
  const badSummary = /\b(independent auditor|auditor'?s report|notes? to the)\b/i;
  const badHeading = /\b(notes? to the)\b/i;

  const summaryMatch = component.cue.exec(summaryPrefix);
  component.cue.lastIndex = 0;
  const headingMatch = component.cue.exec(headingPrefix);
  component.cue.lastIndex = 0;
  const startsWithCue = summaryMatch?.index !== undefined && summaryMatch.index <= 42;
  const headingIsCue = headingMatch?.index !== undefined && headingMatch.index <= 180;

  if (
    hardReject.test(summaryPrefix)
    || hardReject.test(headingPrefix)
    || (
      !startsWithCue
      && (
        isNavigationOrCover(candidate.result)
        || badSummary.test(summaryPrefix)
        || badHeading.test(headingPrefix)
      )
    )
  ) return -1;

  // Do not fill a statement slot from a loose mention inside a note. The
  // indexed summary or the page's opening heading must identify the section.
  if (!startsWithCue && !headingIsCue) return -1;

  let score = startsWithCue ? 120 : 105;
  if (headingIsCue) score += 20;
  if (/\bconsolidated\b/i.test(summaryPrefix.slice(0, 80)) || /\bconsolidated\b/i.test(headingPrefix.slice(0, 220))) score += 16;
  if (/\bunconsolidated\b/i.test(summaryPrefix.slice(0, 80)) || /\bunconsolidated\b/i.test(headingPrefix.slice(0, 220))) score += 6;
  if (/\bin USD\b/i.test(headingPrefix)) score -= 24;
  if ((textHead.match(/\|/g) || []).length >= 6) score += 10;
  if (/\b(PKR|Rs\.?|rupees|assets|liabilities|profit|income|equity|cash)\b/i.test(summaryPrefix)) score += 4;
  return score;
}

async function searchFinancialStatementPack(
  db: D1DatabaseLike,
  filters: SearchFilters,
): Promise<SearchResult[]> {
  const tickerPlaceholders = filters.tickers.map(() => "?").join(", ");
  const yearPlaceholders = filters.years.map(() => "?").join(", ");
  const clauses = [
    "filings_fts_new MATCH ?",
    `d.ticker IN (${tickerPlaceholders})`,
    `substr(d.filing_period, 1, 4) IN (${yearPlaceholders})`,
  ];
  const bindings: unknown[] = [
    FINANCIAL_STATEMENT_FTS_QUERY,
    ...filters.tickers,
    ...filters.years.map(String),
  ];
  if (filters.filingCadence !== "any") {
    clauses.push("d.filing_type = ?");
    bindings.push(filters.filingCadence);
  }
  dateClauses(filters, "d.filing_period", clauses, bindings);
  bindings.push(Math.min(900, Math.max(120, filters.tickers.length * filters.years.length * 60)));

  const headingClauses = [
    `d.ticker IN (${tickerPlaceholders})`,
    `substr(d.filing_period, 1, 4) IN (${yearPlaceholders})`,
  ];
  const headingBindings: unknown[] = [
    ...filters.tickers,
    ...filters.years.map(String),
  ];
  if (filters.filingCadence !== "any") {
    headingClauses.push("d.filing_type = ?");
    headingBindings.push(filters.filingCadence);
  }
  dateClauses(filters, "d.filing_period", headingClauses, headingBindings);
  headingClauses.push(`(
    lower(substr(d.text, 1, 700)) LIKE '%statement of financial position%'
    OR lower(substr(d.text, 1, 700)) LIKE '%profit and loss account%'
    OR lower(substr(d.text, 1, 700)) LIKE '%statement of profit or loss%'
    OR lower(substr(d.text, 1, 700)) LIKE '%cash flow statement%'
    OR lower(substr(d.text, 1, 700)) LIKE '%statement of cash flows%'
    OR lower(substr(d.text, 1, 700)) LIKE '%statement of changes in equity%'
  )`);

  // One BM25 pass retrieves candidates for the whole request. A cheap heading
  // scan recovers strongly labelled pages pushed below the BM25 cutoff by notes
  // that repeat the same phrase. Both feed the same strict page selector.
  const [ftsRows, headingRows] = await Promise.all([
    queryRows<Record<string, unknown>>(
      db,
      `SELECT
         d.id,
         d.ticker,
         d.filing_type,
         d.filing_period,
         d.pg,
         d.jpg_path,
         d.section_tags,
         d.search_text,
         substr(d.text, 1, 1200) AS text_head,
         snippet(filings_fts_new, 1, '⟦', '⟧', ' … ', 24) AS snippet,
         bm25(filings_fts_new, 1.4, 1.0) AS score
       FROM filings_fts_new
       JOIN financial_documents_new d ON d.id = filings_fts_new.rowid
       WHERE ${clauses.join(" AND ")}
       ORDER BY score
       LIMIT ?`,
      bindings,
    ),
    queryRows<Record<string, unknown>>(
      db,
      `SELECT
         d.id,
         d.ticker,
         d.filing_type,
         d.filing_period,
         d.pg,
         d.jpg_path,
         d.section_tags,
         d.search_text,
         substr(d.text, 1, 1200) AS text_head,
         substr(d.text, 1, 500) AS snippet,
         0 AS score
       FROM financial_documents_new d
       WHERE ${headingClauses.join(" AND ")}
       ORDER BY d.ticker, d.filing_period, d.pg`,
      headingBindings,
    ),
  ]);

  const mapCandidate = (
    row: Record<string, unknown>,
    retrievalBasis: "bm25" | "indexed_heading",
  ): StatementCandidate => {
    const ticker = String(row.ticker || "").toUpperCase();
    const page = Number(row.pg) || 0;
    const filingType = String(row.filing_type || "filing");
    const jpgPath = String(row.jpg_path || "");
    const tags = parseTags(row.section_tags);
    return {
      textHead: normalizeText(row.text_head, 1_200),
      result: {
        id: Number(row.id),
        corpus: "filing",
        entity: ticker,
        source_name: ticker,
        title: tags[0] || `${filingType[0]?.toUpperCase() || ""}${filingType.slice(1)} report — page ${page}`,
        period: String(row.filing_period || ""),
        page_number: page,
        summary: normalizeText(row.search_text),
        snippet: normalizeText(row.snippet, 500),
        bm25_score: Math.abs(Number(row.score) || 0),
        source_url: jpgPath ? `${SOURCE_BASE_URL}/PDF_PAGES/${jpgPath.replace(/\.jpg$/i, ".pdf")}` : null,
        thumbnail_url: jpgPath ? `${SOURCE_BASE_URL}/PDF_THUMBNAILS/${jpgPath}` : null,
        asset_key: jpgPath ? `PDF_PAGES/${jpgPath.replace(/\.jpg$/i, ".pdf")}` : null,
        tags,
        quality_flags: [],
        retrieval_basis: retrievalBasis,
        retrieval_lanes: [retrievalBasis === "indexed_heading" ? "indexed_heading" : "summary"],
      },
    };
  };

  const candidateMap = new Map<number, StatementCandidate>();
  for (const row of headingRows) {
    const candidate = mapCandidate(row, "indexed_heading");
    candidateMap.set(candidate.result.id, candidate);
  }
  for (const row of ftsRows) {
    const candidate = mapCandidate(row, "bm25");
    candidateMap.set(candidate.result.id, candidate);
  }
  const candidates = [...candidateMap.values()];

  const rowsByScope = new Map<string, StatementCandidate[]>();
  for (const candidate of candidates) {
    const year = candidate.result.period.slice(0, 4);
    const key = `${candidate.result.entity}:${year}`;
    const current = rowsByScope.get(key) || [];
    current.push(candidate);
    rowsByScope.set(key, current);
  }

  const selectedRows: SearchResult[] = [];
  for (const ticker of filters.tickers) {
    for (const year of filters.years) {
      const scoped = rowsByScope.get(`${ticker}:${year}`) || [];
      for (const component of FINANCIAL_STATEMENT_PACK) {
        const ranked = scoped
          .map((candidate) => ({
            candidate,
            selectorScore: statementCandidateScore(candidate, component),
          }))
          .filter((item) => item.selectorScore > 0)
          .sort((left, right) =>
            right.selectorScore - left.selectorScore
            || right.candidate.result.bm25_score - left.candidate.result.bm25_score,
          );
        const selected = ranked[0]?.candidate.result;
        if (!selected) continue;
        selectedRows.push({
          ...selected,
          title: component.label,
          result_kind: component.kind,
          relevance_reason: selected.retrieval_basis === "bm25"
            ? `Indexed heading match for ${ticker} ${year}; selected from the BM25 candidate pass`
            : `Indexed heading match for ${ticker} ${year}; recovered below the BM25 candidate cutoff`,
        });
      }
    }
  }

  const deduped = new Map<string, SearchResult>();
  for (const result of selectedRows) {
    const key = `${result.corpus}:${result.id}`;
    if (!deduped.has(key)) deduped.set(key, result);
  }
  const tickerOrder = new Map(filters.tickers.map((ticker, index) => [ticker, index]));
  const kindOrder = new Map(FINANCIAL_STATEMENT_PACK.map((component, index) => [component.kind, index]));
  return [...deduped.values()].sort((left, right) =>
    (tickerOrder.get(left.entity) ?? 999) - (tickerOrder.get(right.entity) ?? 999)
    || left.period.localeCompare(right.period)
    || (kindOrder.get(left.result_kind as typeof FINANCIAL_STATEMENT_PACK[number]["kind"]) ?? 999)
      - (kindOrder.get(right.result_kind as typeof FINANCIAL_STATEMENT_PACK[number]["kind"]) ?? 999),
  );
}

async function searchFilings(
  db: D1DatabaseLike,
  structureDb: D1DatabaseLike | undefined,
  queries: FieldQueryPlan,
  limit: number,
  filters: SearchFilters,
): Promise<SearchResult[]> {
  if (filters.queryPack === "financial_statements" && filters.tickers.length > 0 && filters.years.length > 0) {
    return searchFinancialStatementPack(db, filters);
  }
  if (filters.tickers.length === 0) {
    return searchFilingsForTicker(db, structureDb, queries, limit, filters);
  }
  if (filters.tickers.length === 1) {
    const ticker = filters.tickers[0];
    const candidates = await searchFilingsForTicker(
      db,
      structureDb,
      queries,
      filters.years.length > 0
        ? Math.min(MAX_RESULTS_PER_SCOPE * filters.years.length, limit * filters.years.length)
        : limit,
      filters,
      ticker,
    );
    return filters.years.length > 0
      ? yearBalancedLimit(candidates, filters.years, limit)
      : candidates;
  }
  if (filters.years.length > 0) {
    const candidates = await searchFilingsForTicker(
      db,
      structureDb,
      queries,
      Math.min(
        96,
        filters.tickers.length * Math.min(MAX_RESULTS_PER_SCOPE * filters.years.length, limit * filters.years.length),
      ),
      filters,
    );
    return filters.tickers.flatMap((ticker) =>
      yearBalancedLimit(candidates.filter((result) => result.entity === ticker), filters.years, limit)
    );
  }
  const candidates = await searchFilingsForTicker(
    db,
    structureDb,
    queries,
    Math.min(96, limit * filters.tickers.length),
    filters,
  );
  return entityBalancedLimit(candidates, limit * filters.tickers.length, limit, false);
}

async function searchAnnouncementsForTicker(
  db: D1DatabaseLike,
  queries: FieldQueryPlan,
  limit: number,
  filters: SearchFilters,
  ticker?: string,
): Promise<SearchResult[]> {
  const candidateLimit = Math.min(24, Math.max(8, limit * 3));
  const runLane = async (
    column: "title" | "search_text" | "text",
    query: string,
  ): Promise<SearchResult[]> => {
    if (!query) return [];
    const clauses = ["psx_announcements_fts MATCH ?"];
    const bindings: unknown[] = [fieldMatch(column, query)];
    if (ticker) {
      clauses.push("a.ticker = ?");
      bindings.push(ticker);
    } else if (filters.tickers.length > 0) {
      clauses.push(`a.ticker IN (${filters.tickers.map(() => "?").join(", ")})`);
      bindings.push(...filters.tickers);
    } else if (filters.industryTerms.length > 0) {
      const scopedTickers = industryTickers(filters.industryTerms);
      if (scopedTickers.length === 0) return [];
      clauses.push(`a.ticker IN (${scopedTickers.map(() => "?").join(", ")})`);
      bindings.push(...scopedTickers);
    }
    dateClauses(filters, "a.announcement_date", clauses, bindings);
    bindings.push(candidateLimit);
    const snippetColumn = column === "title" ? 1 : column === "search_text" ? 2 : 3;
    const weights = column === "title"
      ? "0.0, 1.0, 0.0, 0.0"
      : column === "search_text"
        ? "0.0, 0.0, 1.0, 0.0"
        : "0.0, 0.0, 0.0, 1.0";
    const rows = await queryRows<Record<string, unknown>>(
      db,
      `SELECT
         a.id,
         a.ticker,
         a.company_name,
         a.announcement_date,
         a.announcement_time,
         a.title,
         a.notice_status,
         a.local_path,
         a.content_type,
         a.direct_url,
         a.source_url,
         a.search_text,
         snippet(psx_announcements_fts, ${snippetColumn}, '⟦', '⟧', ' … ', 24) AS snippet,
         bm25(psx_announcements_fts, ${weights}) AS score
       FROM psx_announcements_fts
       JOIN psx_announcements a ON a.id = psx_announcements_fts.rowid
       WHERE ${clauses.join(" AND ")}
       ORDER BY score
       LIMIT ?`,
      bindings,
    );
    return rows.map(announcementResultFromRow);
  };

  const [strictTitle, strictSummary, strictText] = await Promise.all([
    runLane("title", queries.bm25.heading),
    runLane("search_text", queries.bm25.summary),
    runLane("text", queries.bm25.text),
  ]);
  const strongLanes: Array<{ lane: RetrievalLane; results: SearchResult[] }> = [
    { lane: "title", results: strictTitle },
    { lane: "summary", results: strictSummary },
    { lane: "text", results: strictText },
  ];
  const lanes = strongLanes;
  for (const lane of lanes) {
    recordLaneCandidates(filters, "announcement", lane.lane, lane.results);
  }
  const fused = !ticker && filters.tickers.length > 1
    ? filters.tickers.flatMap((entity) =>
        fuseRankedLanes(
          lanes.map((lane) => ({
            ...lane,
            results: lane.results.filter((result) => result.entity === entity),
          })),
          Math.max(8, Math.ceil(limit / filters.tickers.length) * 3),
        )
      )
    : fuseRankedLanes(lanes, ticker ? limit : limit * 4);
  if (ticker || filters.tickers.length > 1) return fused;
  return entityBalancedLimit(fused, limit);
}

async function searchAnnouncements(
  db: D1DatabaseLike,
  queries: FieldQueryPlan,
  limit: number,
  filters: SearchFilters,
): Promise<SearchResult[]> {
  if (filters.tickers.length === 0) {
    return searchAnnouncementsForTicker(db, queries, limit, filters);
  }
  if (filters.tickers.length === 1) {
    return searchAnnouncementsForTicker(db, queries, limit, filters, filters.tickers[0]);
  }
  const candidates = await searchAnnouncementsForTicker(
    db,
    queries,
    Math.min(48, limit * filters.tickers.length),
    filters,
  );
  return entityBalancedLimit(candidates, limit * filters.tickers.length, limit, false);
}

async function searchMacro(
  db: D1DatabaseLike,
  queries: FieldQueryPlan,
  limit: number,
  filters: SearchFilters,
): Promise<SearchResult[]> {
  // Macro rows have no company/ticker dimension. A ticker filter must not leak
  // unrelated macro pages into a company-scoped result set.
  if (filters.tickers.length > 0) return [];

  const candidateLimit = Math.min(24, Math.max(8, limit * 3));
  const runLane = async (
    column: "chapter" | "content",
    query: string,
  ): Promise<SearchResult[]> => {
    if (!query) return [];
    const clauses = ["macro_fts MATCH ?"];
    const bindings: unknown[] = [fieldMatch(column, query)];
    if (filters.dateFrom) {
      clauses.push("CAST(substr(d.fiscal_year, 1, 4) AS INTEGER) >= ?");
      bindings.push(Number(filters.dateFrom.slice(0, 4)));
    }
    if (filters.dateTo) {
      clauses.push("CAST(substr(d.fiscal_year, 1, 4) AS INTEGER) <= ?");
      bindings.push(Number(filters.dateTo.slice(0, 4)));
    }
    if (filters.latest) {
      clauses.push(
        `CAST(substr(d.fiscal_year, 1, 4) AS INTEGER) = (
          SELECT MAX(CAST(substr(latest.fiscal_year, 1, 4) AS INTEGER))
          FROM macro_documents latest
          WHERE latest.doc_type = d.doc_type
        )`,
      );
    }
    bindings.push(candidateLimit);
    const snippetColumn = column === "chapter" ? 2 : 4;
    const weights = column === "chapter"
      ? "0.0, 0.0, 1.0, 0.0, 0.0"
      : "0.0, 0.0, 0.0, 0.0, 1.0";
    const rows = await queryRows<Record<string, unknown>>(
      db,
      `SELECT
         d.id,
         d.doc_type,
         d.fiscal_year,
         d.source_doc,
         d.chapter,
         d.page_num,
         d.content_type,
         snippet(macro_fts, ${snippetColumn}, '⟦', '⟧', ' … ', 24) AS snippet,
         bm25(macro_fts, ${weights}) AS score
       FROM macro_fts
       JOIN macro_documents d ON d.id = macro_fts.rowid
       WHERE ${clauses.join(" AND ")}
       ORDER BY score
       LIMIT ?`,
      bindings,
    );
    return rows.map(macroResultFromRow);
  };

  const [strictChapter, strictText] = await Promise.all([
    runLane("chapter", queries.bm25.heading),
    runLane("content", queries.bm25.text),
  ]);
  const strongLanes: Array<{ lane: RetrievalLane; results: SearchResult[] }> = [
    { lane: "chapter", results: strictChapter },
    { lane: "text", results: strictText },
  ];
  const lanes = strongLanes;
  for (const lane of lanes) {
    recordLaneCandidates(filters, "macro", lane.lane, lane.results);
  }
  return fuseRankedLanes(lanes, limit);
}

async function attachQualityFlags(db: D1DatabaseLike, results: SearchResult[]): Promise<void> {
  const tickers = [...new Set(
    results
      .filter((row) => row.corpus !== "macro" && /^[A-Z0-9.-]{1,16}$/.test(row.entity))
      .map((row) => row.entity),
  )];
  if (tickers.length === 0) return;

  const placeholders = tickers.map(() => "?").join(", ");
  const flags = await db.prepare(
    `SELECT flag_id, ticker, severity, status, category, affected_table,
            filing_period, period_end, metric, rule, message, generated_at
     FROM data_quality_flags
     WHERE ticker IN (${placeholders})
       AND lower(COALESCE(status, 'open')) NOT IN ('closed', 'resolved', 'dismissed')
     ORDER BY
       CASE lower(COALESCE(severity, '')) WHEN 'blocker' THEN 0 WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,
       generated_at DESC
     LIMIT 100`,
  ).bind(...tickers).all<QualityFlag>();

  for (const result of results) {
    result.quality_flags = (flags.results || []).filter((flag) => {
      if (flag.ticker !== result.entity) return false;
      const flagPeriod = flag.filing_period || flag.period_end;
      return !flagPeriod || !result.period || result.period.startsWith(flagPeriod);
    }).slice(0, 5);
  }
}

function evidenceScore(
  result: SearchResult,
  inputs: FieldQueries,
  literalQuery: string,
  corpusRank: number,
): number {
  const headingEvidence = [
    result.title,
    result.derived_heading,
    ...(result.table_signature || []),
  ].filter(Boolean).join(" ");
  const summaryEvidence = [result.title, result.summary].filter(Boolean).join(" ");
  const textEvidence = [
    result.title,
    result.summary,
    result.snippet,
    ...(result.table_signature || []),
  ].filter(Boolean).join(" ");
  const coverages = [
    tokenCoverage(inputs.heading, headingEvidence),
    tokenCoverage(inputs.summary, summaryEvidence),
    tokenCoverage(inputs.text, textEvidence),
  ].sort((left, right) => right - left);
  // A single exact-looking field is fragile: a related filing heading can be
  // a worse result than a candidate independently supported by title, summary,
  // and body. Reward cross-representation agreement more than one-field peaks.
  const consensusCoverage = coverages[0] * 0.15 + coverages[1] * 0.5 + coverages[2] * 0.35;
  const overallCoverage = tokenCoverage(
    `${inputs.summary} ${inputs.text} ${inputs.heading}`,
    textEvidence,
  );
  const literalCoverage = tokenCoverage(
    literalQuery,
    `${result.entity} ${headingEvidence}`,
  );
  const baseLanes = new Set(
    (result.retrieval_lanes || []).map((lane) => lane.replace(/_phrase$/, "")),
  );
  const availableLaneFamilies = result.corpus === "macro" ? 2 : 3;
  const laneAgreement = Math.min(1, baseLanes.size / availableLaneFamilies);
  const phraseAgreement = Math.min(
    1,
    (result.retrieval_lanes || []).filter((lane) => lane.endsWith("_phrase")).length / 2,
  );
  const rankEvidence = 1 / Math.max(1, corpusRank);
  const navigationPenalty = isNavigationOrCover(result) ? 0.3 : 0;
  return (
    consensusCoverage * 0.23
    + overallCoverage * 0.08
    + literalCoverage * 0.2
    + laneAgreement * 0.36
    + phraseAgreement * 0.05
    + rankEvidence * 0.08
    - navigationPenalty
  );
}

/**
 * Compare candidates through observable query-to-evidence agreement. Raw BM25
 * scores are deliberately excluded because scores from different FTS tables
 * and field lengths do not share a meaningful scale.
 */
export function rankAcrossCorpora(
  corpusResults: Array<{ corpus: CorpusKind; results: SearchResult[] }>,
  inputs: FieldQueries,
  literalQuery: string,
  limit: number,
  preferRecent: boolean,
): SearchResult[] {
  const periodOrdinal = (period: string): number => {
    const dated = Date.parse(period);
    if (Number.isFinite(dated)) return dated;
    const year = Number(period.slice(0, 4));
    return Number.isInteger(year) && year >= 1900 && year <= 2100
      ? Date.UTC(year, 0, 1)
      : Number.NaN;
  };
  const scored = corpusResults.flatMap(({ corpus, results }) =>
    results.map((result, index) => ({
      result,
      corpus,
      baseScore: evidenceScore(result, inputs, literalQuery, index + 1),
    })),
  );
  const supportByCorpus = new Map<CorpusKind, number>();
  for (const corpus of ["filing", "announcement", "macro"] as const) {
    const strongest = scored
      .filter((item) => item.corpus === corpus)
      .map((item) => item.baseScore)
      .sort((left, right) => right - left)
      .slice(0, 3);
    supportByCorpus.set(
      corpus,
      strongest.length ? strongest.reduce((sum, value) => sum + value, 0) / strongest.length : 0,
    );
  }
  const dated = scored
    .map((item) => periodOrdinal(item.result.period))
    .filter(Number.isFinite);
  const newest = dated.length ? Math.max(...dated) : 0;
  const oldest = dated.length ? Math.min(...dated) : 0;
  const strongestCorpusSupport = Math.max(0, ...supportByCorpus.values());
  const eligibleCorpora = new Set(
    [...supportByCorpus.entries()]
      .filter(([, support]) =>
        support > 0
        && (strongestCorpusSupport === 0 || support >= strongestCorpusSupport * 0.85)
      )
      .map(([corpus]) => corpus),
  );

  return scored
    .filter(({ corpus }) => eligibleCorpora.has(corpus))
    .map(({ result, corpus, baseScore }) => {
      const parsedDate = periodOrdinal(result.period);
      const recency = preferRecent && Number.isFinite(parsedDate) && newest > oldest
        ? (parsedDate - oldest) / (newest - oldest)
        : 0;
      const score = baseScore + (supportByCorpus.get(corpus) || 0) * 0.1 + recency * 0.05;
      return {
        ...result,
        evidence_score: Number(score.toFixed(6)),
        corpus_support: Number((supportByCorpus.get(corpus) || 0).toFixed(6)),
      };
    })
    .sort((left, right) =>
      (right.evidence_score || 0) - (left.evidence_score || 0)
      || (right.fusion_score || 0) - (left.fusion_score || 0)
      || String(right.period).localeCompare(String(left.period)),
    )
    .slice(0, limit);
}

interface KeyCandidate extends Record<string, unknown> {
  page_id: number;
  corpus: CorpusKind;
  score: number;
  display_summary: string;
}

async function queryKeyCandidates(
  keyDb: D1DatabaseLike,
  corpus: CorpusKind,
  query: string,
  limit: number,
  filters: SearchFilters,
): Promise<KeyCandidate[]> {
  if (!query) return [];
  const clauses = [
    "page_keys_fts MATCH ?",
    "k.corpus = ?",
  ];
  const bindings: unknown[] = [query, corpus];
  if (corpus !== "macro") {
    if (filters.tickers.length > 0) {
      clauses.push(`k.entity IN (${filters.tickers.map(() => "?").join(", ")})`);
      bindings.push(...filters.tickers);
    } else if (filters.industryTerms.length > 0) {
      const scopedTickers = industryTickers(filters.industryTerms);
      if (scopedTickers.length === 0) return [];
      clauses.push(`k.entity IN (${scopedTickers.map(() => "?").join(", ")})`);
      bindings.push(...scopedTickers);
    }
  }
  if (corpus === "filing" && filters.years.length > 0) {
    clauses.push(`substr(k.period, 1, 4) IN (${filters.years.map(() => "?").join(", ")})`);
    bindings.push(...filters.years.map(String));
  } else {
    if (filters.dateFrom) {
      clauses.push("CAST(substr(k.period, 1, 4) AS INTEGER) >= ?");
      bindings.push(Number(filters.dateFrom.slice(0, 4)));
    }
    if (filters.dateTo) {
      clauses.push("CAST(substr(k.period, 1, 4) AS INTEGER) <= ?");
      bindings.push(Number(filters.dateTo.slice(0, 4)));
    }
  }
  if (corpus === "filing" && filters.filingCadence !== "any") {
    clauses.push("k.document_type = ?");
    bindings.push(filters.filingCadence);
  }
  if (corpus === "filing" && filters.latest) {
    const latestScopes = Object.entries(filters.latestPeriods);
    if (latestScopes.length > 0) {
      clauses.push(`(${latestScopes.map(() => "(k.entity = ? AND k.period = ?)").join(" OR ")})`);
      for (const [ticker, period] of latestScopes) bindings.push(ticker, period);
    }
  }
  const candidateLimit = Math.min(96, Math.max(24, limit * 6));
  bindings.push(candidateLimit);
  return queryRows<KeyCandidate>(
    keyDb,
    `SELECT
       k.corpus,
       k.page_id,
       k.entity,
       k.industry,
       k.period,
       k.document_type,
       k.publisher,
       k.schema_version,
       k.display_summary,
       k.parse_status,
       k.metadata_alignment,
       bm25(page_keys_fts, 1.0, 1.0) AS score
     FROM page_keys_fts
     JOIN page_keys k ON k.id = page_keys_fts.rowid
     WHERE ${clauses.join(" AND ")}
     ORDER BY score
     LIMIT ?`,
    bindings,
  );
}

async function hydrateKeyCandidates(
  db: D1DatabaseLike,
  corpus: CorpusKind,
  candidates: KeyCandidate[],
  queries: FieldQueryPlan,
): Promise<SearchResult[]> {
  if (candidates.length === 0) return [];
  const ids = candidates.map((candidate) => Number(candidate.page_id));
  const placeholders = ids.map(() => "?").join(", ");
  let rows: Record<string, unknown>[];
  if (corpus === "filing") {
    rows = await queryRows<Record<string, unknown>>(
      db,
      `SELECT id, ticker, industry, filing_type, filing_period, pg, jpg_path,
              section_tags, search_text
       FROM financial_documents_new
       WHERE id IN (${placeholders})`,
      ids,
    );
  } else if (corpus === "announcement") {
    rows = await queryRows<Record<string, unknown>>(
      db,
      `SELECT id, ticker, company_name, announcement_date, announcement_time,
              title, notice_status, local_path, content_type, direct_url,
              source_url, search_text
       FROM psx_announcements
       WHERE id IN (${placeholders})`,
      ids,
    );
  } else {
    rows = await queryRows<Record<string, unknown>>(
      db,
      `SELECT id, doc_type, fiscal_year, source_doc, chapter, page_num, content_type
       FROM macro_documents
       WHERE id IN (${placeholders})`,
      ids,
    );
  }
  const rowsById = new Map(rows.map((row) => [Number(row.id), row]));
  return candidates.flatMap((candidate) => {
    const row = rowsById.get(Number(candidate.page_id));
    if (!row) return [];
    const enriched = {
      ...row,
      score: candidate.score,
      snippet: candidate.display_summary,
      schema_version: candidate.schema_version,
      display_summary: candidate.display_summary,
      parse_status: candidate.parse_status,
      metadata_alignment: candidate.metadata_alignment,
    };
    if (corpus === "filing") return [filingResultFromRow(enriched, queries)];
    if (corpus === "announcement") return [announcementResultFromRow(enriched)];
    return [macroResultFromRow(enriched)];
  });
}

async function searchKeyLane(
  keyDb: D1DatabaseLike,
  db: D1DatabaseLike,
  corpus: CorpusKind,
  queries: FieldQueryPlan,
  limit: number,
  filters: SearchFilters,
): Promise<SearchResult[]> {
  const started = performance.now();
  const candidates = await queryKeyCandidates(
    keyDb,
    corpus,
    queries.bm25.key || "",
    limit,
    filters,
  );
  const results = await hydrateKeyCandidates(db, corpus, candidates, queries);
  recordLaneCandidates(filters, corpus, "key", results);
  if (filters.diagnostics) {
    filters.diagnostics[`${corpus}_key_lane_ms`] = Number((performance.now() - started).toFixed(2));
    filters.diagnostics[`${corpus}_key_candidate_count`] = results.length;
  }
  return results;
}

async function runSearch(
  db: D1DatabaseLike,
  keyDb: D1DatabaseLike | undefined,
  queries: FieldQueryPlan,
  literalQuery: string,
  limit: number,
  filters: SearchFilters,
): Promise<SearchResult[]> {
  const retrievalStarted = performance.now();
  if (filters.retrievalMode !== "baseline" && !keyDb) {
    throw new Error("The requested retrieval mode requires the search sidecar binding.");
  }
  const scopes = filters.corpora.map((corpus) => ({
    corpus,
    filters: corpus === "macro"
      ? { ...filters, tickers: [], latestPeriods: {} }
      : filters,
  }));
  const baselineByCorpus = new Map<CorpusKind, SearchResult[]>();
  if (filters.retrievalMode !== "key_only") {
    const baselineResults = await Promise.all(scopes.map(async ({ corpus, filters: scopedFilters }) => {
      if (corpus === "filing") {
        return { corpus, results: await searchFilings(db, keyDb, queries, limit, scopedFilters) };
      }
      if (corpus === "announcement") {
        return { corpus, results: await searchAnnouncements(db, queries, limit, scopedFilters) };
      }
      return { corpus, results: await searchMacro(db, queries, limit, scopedFilters) };
    }));
    for (const entry of baselineResults) baselineByCorpus.set(entry.corpus, entry.results);
  }
  const keyByCorpus = new Map<CorpusKind, SearchResult[]>();
  if (
    (filters.retrievalMode === "key_only" || filters.retrievalMode === "combined")
    && keyDb
  ) {
    const keyResults = await Promise.all(scopes.map(async ({ corpus, filters: scopedFilters }) => ({
      corpus,
      results: await searchKeyLane(keyDb, db, corpus, queries, limit, scopedFilters),
    })));
    for (const entry of keyResults) keyByCorpus.set(entry.corpus, entry.results);
  }
  const resultLimit = limit * Math.max(1, filters.tickers.length);
  const corpusResults = scopes.map(({ corpus }) => {
    const baseline = baselineByCorpus.get(corpus) || [];
    const key = keyByCorpus.get(corpus) || [];
    if (filters.retrievalMode === "baseline" || filters.retrievalMode === "structure") {
      return { corpus, results: baseline };
    }
    if (filters.retrievalMode === "key_only") {
      return { corpus, results: fuseRankedLanes([{ lane: "key", results: key }], resultLimit) };
    }
    return {
      corpus,
      results: fuseRankedLanes([
        { lane: "baseline", results: baseline },
        { lane: "key", results: key },
      ], resultLimit),
    };
  });
  if (filters.diagnostics) {
    filters.diagnostics.corpus_retrieval_ms = Number((performance.now() - retrievalStarted).toFixed(2));
  }
  const ranked = rankAcrossCorpora(
    corpusResults,
    queries.inputs,
    literalQuery,
    resultLimit,
    filters.latest || filters.preferRecent,
  );
  const results = assembleCoverageAwareResults(
    ranked,
    filters.requiredTickers,
    resultLimit,
  );
  const flagsStarted = performance.now();
  await attachQualityFlags(db, results);
  if (filters.diagnostics) {
    filters.diagnostics.quality_flags_ms = Number((performance.now() - flagsStarted).toFixed(2));
    filters.diagnostics.search_total_ms = Number((performance.now() - retrievalStarted).toFixed(2));
  }
  return results;
}

function financialStatementCoverageGaps(
  filters: SearchFilters,
  results: SearchResult[],
): Array<{ ticker: string; year: number; missing_sections: string[] }> {
  if (filters.queryPack !== "financial_statements") return [];
  const gaps: Array<{ ticker: string; year: number; missing_sections: string[] }> = [];
  for (const ticker of filters.tickers) {
    for (const year of filters.years) {
      const foundKinds = new Set(
        results
          .filter((result) =>
            result.corpus === "filing"
            && result.entity === ticker
            && result.period.startsWith(String(year)),
          )
          .map((result) => result.result_kind)
          .filter(Boolean),
      );
      const missingSections = FINANCIAL_STATEMENT_PACK
        .filter((component) => !foundKinds.has(component.kind))
        .map((component) => component.label);
      if (missingSections.length > 0) {
        gaps.push({ ticker, year, missing_sections: missingSections });
      }
    }
  }
  return gaps;
}

async function handleStats(env: Env): Promise<Response> {
  const counts = await env.DB.prepare(
    `SELECT 'filing' AS corpus, COUNT(*) AS rows FROM financial_documents_new
     UNION ALL SELECT 'announcement', COUNT(*) FROM psx_announcements
     UNION ALL SELECT 'macro', COUNT(*) FROM macro_documents`,
  ).all<{ corpus: CorpusKind; rows: number }>();
  const rows = counts.results || [];
  return json({
    corpora: rows,
    total_documents: rows.reduce((sum, row) => sum + Number(row.rows || 0), 0),
    retrieval_version: RETRIEVAL_VERSION,
    ranking: "Controller-scoped field-specific SQLite FTS5 BM25 + reciprocal-rank fusion + structural reranking",
    source_assets: "Cloudflare R2",
  });
}

async function handleTickers(env: Env): Promise<Response> {
  const rows = await env.DB.prepare(
    `SELECT DISTINCT ticker, industry
     FROM financial_documents_new
     WHERE ticker IS NOT NULL AND ticker != ''
     ORDER BY ticker`,
  ).all<{ ticker: string; industry?: string | null }>();
  return json({
    tickers: (rows.results || []).map((row) => row.ticker),
    industries: [...new Set(
      (rows.results || [])
        .map((row) => String(row.industry || INDUSTRY_BY_TICKER.get(row.ticker) || "").trim())
        .filter(Boolean),
    )].sort((left, right) => left.localeCompare(right)),
  });
}

async function handleEntities(env: Env): Promise<Response> {
  const entities = (TICKERS_JSON as Array<Record<string, unknown>>)
    .map((row) => ({
      ticker: String(row.Symbol || "").trim().toUpperCase(),
      company_name: String(row["Company Name"] || "").trim(),
      industry: String(row.Industry || "").trim(),
    }))
    .filter((row) => row.ticker && row.company_name);
  const known = new Set(entities.map((entity) => entity.ticker));
  const current = await env.DB.prepare(
    `SELECT DISTINCT ticker
     FROM financial_documents_new
     WHERE ticker IS NOT NULL AND ticker != ''
     ORDER BY ticker`,
  ).all<{ ticker: string }>();
  for (const row of current.results || []) {
    if (!known.has(row.ticker)) {
      entities.push({ ticker: row.ticker, company_name: row.ticker, industry: "" });
    }
  }
  entities.sort((left, right) => left.ticker.localeCompare(right.ticker));
  return json({ entities });
}

async function handleSearch(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const query = (url.searchParams.get("q") || "").trim();
  const literalQuery = (url.searchParams.get("q_literal") || query).trim();
  const limit = Math.max(1, Math.min(MAX_RESULTS_PER_SCOPE, Number(url.searchParams.get("limit")) || 5));
  const filters = parseSearchFilters(url);
  if (filters.latest && filters.corpora.includes("filing")) {
    const scopeTickers = filters.tickers.length > 0
      ? filters.tickers
      : industryTickers(filters.industryTerms);
    if (scopeTickers.length > 0) {
      const clauses = [`ticker IN (${scopeTickers.map(() => "?").join(", ")})`];
      const bindings: unknown[] = [...scopeTickers];
      if (filters.filingCadence !== "any") {
        clauses.push("filing_type = ?");
        bindings.push(filters.filingCadence);
      }
      const rows = await queryRows<{ ticker: string; period: string }>(
        env.DB,
        `SELECT ticker, MAX(filing_period) AS period
         FROM financial_documents_new
         WHERE ${clauses.join(" AND ")}
         GROUP BY ticker`,
        bindings,
      );
      filters.latestPeriods = Object.fromEntries(
        rows
          .filter((row) => row.ticker && row.period)
          .map((row) => [row.ticker, row.period]),
      );
    }
  }
  const fieldInputs = {
    key: (url.searchParams.get("q_key") || query).trim(),
    summary: (url.searchParams.get("q_summary") || query).trim(),
    text: (url.searchParams.get("q_text") || query).trim(),
    heading: (url.searchParams.get("q_heading") || query).trim(),
  };
  const fieldFts = {
    key: buildFtsQueries(fieldInputs.key),
    summary: buildFtsQueries(fieldInputs.summary),
    text: buildFtsQueries(fieldInputs.text),
    heading: buildFtsQueries(fieldInputs.heading),
  };
  const parsedTerms = [...new Set([
    ...fieldFts.key.tokens,
    ...fieldFts.summary.tokens,
    ...fieldFts.text.tokens,
    ...fieldFts.heading.tokens,
  ])];
  if (parsedTerms.length === 0) return json({ error: "Enter at least one searchable word." }, 400);
  const bm25Queries: FieldQueries = {
    key: fieldFts.key.any,
    summary: fieldFts.summary.any,
    text: fieldFts.text.any,
    heading: fieldFts.heading.any,
  };
  const queryPlan: FieldQueryPlan = {
    inputs: fieldInputs,
    bm25: bm25Queries,
    exactKey: [
      `(${fieldFts.summary.all})`,
      `(${fieldFts.text.all})`,
    ].join(" OR "),
  };
  const results = await runSearch(env.DB, env.KEY_DB, queryPlan, literalQuery, limit, filters);
  const coverageGaps = financialStatementCoverageGaps(filters, results);
  const resultFacets: CatalogFacet[] = buildCatalogFacets(results, filters);

  return json({
    catalog_schema_version: CATALOG_SCHEMA.version,
    retrieval_version: RETRIEVAL_VERSION,
    query,
    literal_query: literalQuery,
    parsed_terms: parsedTerms,
    match_mode: "any",
    ranking: filters.corpora.length > 1
      ? "Controller-scoped field-specific FTS5 BM25 + reciprocal-rank fusion"
      : "Field-specific FTS5 BM25 + reciprocal-rank fusion + structural reranking",
    retrieval_strategy: filters.corpora.length > 1
      ? "evidence_scout_rrf_v2"
      : "field_rrf_v1",
    field_queries: fieldInputs,
    broadening_applied: false,
    retrieval_mode: filters.retrievalMode,
    diagnostics: filters.diagnostics
      ? {
          timings: filters.diagnostics,
          lanes: filters.laneDiagnostics,
        }
      : undefined,
    latest_periods: filters.latestPeriods,
    result_count: results.length,
    coverage_gaps: coverageGaps,
    result_facets: resultFacets,
    limit_per_scope: limit,
    filters: {
      tickers: filters.tickers,
      required_tickers: filters.requiredTickers,
      industries: filters.industryTerms,
      years: filters.years,
      date_from: filters.dateFrom,
      date_to: filters.dateTo,
      corpora: filters.corpora,
      filing_cadence: filters.filingCadence,
      latest: filters.latest,
      prefer_recent: filters.preferRecent,
      query_pack: filters.queryPack,
      retrieval_mode: filters.retrievalMode,
    },
    results,
  });
}

async function handleSimilar(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const corpus = (url.searchParams.get("corpus") || "") as CorpusKind;
  const id = Number(url.searchParams.get("id"));
  const mode = url.searchParams.get("mode") === "history" ? "history" : "peers";
  const limit = Math.max(1, Math.min(8, Number(url.searchParams.get("limit")) || 5));
  if (!["filing", "announcement", "macro"].includes(corpus) || !Number.isInteger(id) || id <= 0) {
    return json({ error: "A valid corpus and document id are required." }, 400);
  }

  const anchor = await fetchDocument(env.DB, corpus, id);
  if (!anchor) return json({ error: "Source page not found." }, 404);
  const prototype = buildSimilarPrototype(
    corpus === "macro" ? anchor.content : anchor.text,
    anchor.search_text,
    url.searchParams.get("active_query") || "",
  );
  const filters = parseSearchFilters(url);
  filters.corpora = [corpus];
  const anchorTicker = String(anchor.ticker || "").toUpperCase();
  if (corpus === "filing" || corpus === "announcement") {
    if (mode === "history" && anchorTicker) {
      filters.tickers = [anchorTicker];
    } else if (filters.tickers.length > 1 && anchorTicker) {
      filters.tickers = filters.tickers.filter((ticker) => ticker !== anchorTicker);
    } else if (filters.tickers.length === 0 && filters.industryTerms.length === 0 && anchorTicker) {
      // With no analyst scope, prefer the same disclosure over time instead of
      // searching every listed company from one click.
      filters.tickers = [anchorTicker];
    }
    filters.requiredTickers = [...filters.tickers];
  }

  const fieldFts = {
    summary: buildFtsQueries(prototype.summary),
    text: buildFtsQueries(prototype.text),
    heading: buildFtsQueries(prototype.heading),
  };
  const inputs: FieldQueries = {
    summary: prototype.summary,
    text: prototype.text,
    heading: prototype.heading,
  };
  const queryPlan: FieldQueryPlan = {
    inputs,
    bm25: {
      summary: fieldFts.summary.any,
      text: fieldFts.text.any,
      heading: fieldFts.heading.any,
    },
    exactKey: [
      `(${fieldFts.summary.all})`,
      `(${fieldFts.text.all})`,
    ].join(" OR "),
  };
  const similarCandidates = (await runSearch(env.DB, env.KEY_DB, queryPlan, prototype.query, limit + 2, filters))
    .filter((result) => !(result.corpus === corpus && result.id === id));
  const results = entityBalancedLimit(
    similarCandidates,
    limit,
    Math.max(1, Math.ceil(limit / Math.max(1, filters.tickers.length))),
  )
    .map((result) => ({
      ...result,
      relevance_reason: `Similar to selected page via ${prototype.signals.title}`,
    }));

  return json({
    mode,
    anchor: {
      id,
      corpus,
      entity: anchorTicker || String(anchor.doc_type || ""),
      heading: prototype.signals.title,
      table_signature: prototype.signals.tableSignature,
    },
    derived_query: prototype.query,
    field_queries: inputs,
    result_count: results.length,
    results,
  });
}

async function fetchDocument(db: D1DatabaseLike, corpus: CorpusKind, id: number): Promise<Record<string, unknown> | null> {
  if (corpus === "filing") {
    return db.prepare(
      `SELECT id, ticker, filing_type, filing_period, pg, jpg_path, section_tags, search_text, text
       FROM financial_documents_new WHERE id = ?`,
    ).bind(id).first<Record<string, unknown>>();
  }
  if (corpus === "announcement") {
    return db.prepare(
      `SELECT id, ticker, company_name, announcement_date, announcement_time, title, notice_status,
              local_path, content_type, direct_url, source_url, search_text, text
       FROM psx_announcements WHERE id = ?`,
    ).bind(id).first<Record<string, unknown>>();
  }
  return db.prepare(
    `SELECT id, doc_type, fiscal_year, source_doc, chapter, page_num, content_type, content AS text
     FROM macro_documents WHERE id = ?`,
  ).bind(id).first<Record<string, unknown>>();
}

function documentAsset(corpus: CorpusKind, row: Record<string, unknown>): {
  assetKey: string | null;
  sourceUrl: string | null;
  thumbnailUrl: string | null;
} {
  if (corpus === "filing") {
    const jpgPath = String(row.jpg_path || "");
    return {
      assetKey: jpgPath ? `PDF_PAGES/${jpgPath.replace(/\.jpg$/i, ".pdf")}` : null,
      sourceUrl: jpgPath ? `${SOURCE_BASE_URL}/PDF_PAGES/${jpgPath.replace(/\.jpg$/i, ".pdf")}` : null,
      thumbnailUrl: jpgPath ? `${SOURCE_BASE_URL}/PDF_THUMBNAILS/${jpgPath}` : null,
    };
  }
  if (corpus === "announcement") {
    const localPath = String(row.local_path || "").replace(/^announcement_documents\//, "");
    const sourceUrl = localPath
      ? `${SOURCE_BASE_URL}/ANNOUNCEMENTS/${localPath}`
      : String(row.direct_url || row.source_url || "") || null;
    return {
      assetKey: localPath ? `ANNOUNCEMENTS/${localPath}` : null,
      sourceUrl,
      thumbnailUrl: localPath && /\.pdf$/i.test(localPath)
        ? `${SOURCE_BASE_URL}/ANNOUNCEMENT_THUMBNAILS/${localPath.replace(/\.pdf$/i, ".jpg")}`
        : sourceUrl,
    };
  }
  const page = Number(row.page_num) || 0;
  const rel = [row.source_doc, row.chapter, `page_${String(page).padStart(3, "0")}`]
    .filter(Boolean)
    .join("/");
  return {
    assetKey: rel ? `MACRO_PAGES/${rel}.pdf` : null,
    sourceUrl: rel ? `${SOURCE_BASE_URL}/MACRO_PAGES/${rel}.pdf` : null,
    thumbnailUrl: rel ? `${SOURCE_BASE_URL}/MACRO_THUMBNAILS/${rel}.jpg` : null,
  };
}

async function handleDocument(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const corpus = url.searchParams.get("corpus") as CorpusKind | null;
  const id = Number(url.searchParams.get("id"));
  if (!corpus || !["filing", "announcement", "macro"].includes(corpus) || !Number.isSafeInteger(id) || id <= 0) {
    return json({ error: "A valid corpus and document id are required." }, 400);
  }

  const row = await fetchDocument(env.DB, corpus, id);
  if (!row) return json({ error: "Document not found." }, 404);

  const asset = documentAsset(corpus, row);
  const object = asset.assetKey ? await env.SOURCE_BUCKET.head(asset.assetKey) : null;
  const ticker = String(row.ticker || "").toUpperCase();
  const period = String(row.filing_period || row.announcement_date || "");
  const shellResult: SearchResult = {
    id,
    corpus,
    entity: ticker,
    source_name: ticker,
    title: String(row.title || ""),
    period,
    page_number: Number(row.pg || row.page_num) || null,
    summary: normalizeText(row.search_text),
    snippet: "",
    bm25_score: 0,
    source_url: asset.sourceUrl,
    thumbnail_url: asset.thumbnailUrl,
    asset_key: asset.assetKey,
    tags: [],
    quality_flags: [],
  };
  await attachQualityFlags(env.DB, [shellResult]);

  return json({
    id,
    corpus,
    text: String(row.text || ""),
    search_text: String(row.search_text || ""),
    metadata: row,
    source_url: asset.sourceUrl,
    thumbnail_url: asset.thumbnailUrl,
    asset: {
      key: asset.assetKey,
      status: object ? "available" : asset.assetKey ? "missing" : "external",
      bytes: object?.size ?? null,
      etag: object?.httpEtag ?? null,
      uploaded_at: object?.uploaded?.toISOString?.() ?? null,
    },
    quality_flags: shellResult.quality_flags,
  });
}

const RELEVANCE_JUDGMENTS = new Set([
  "ideal",
  "relevant",
  "supporting",
  "irrelevant",
  "wrong_scope",
  "navigation_noise",
]);

async function handleFeedback(request: Request, env: Env): Promise<Response> {
  let payload: Record<string, unknown>;
  try {
    payload = await request.json() as Record<string, unknown>;
  } catch {
    return json({ error: "Feedback must be valid JSON." }, 400, "no-store");
  }
  const judgment = String(payload.judgment || "").trim();
  const corpus = String(payload.corpus || "").trim();
  const pageId = Number(payload.page_id);
  const query = normalizeText(payload.query, 2_000);
  if (
    !RELEVANCE_JUDGMENTS.has(judgment)
    || !["filing", "announcement", "macro"].includes(corpus)
    || !Number.isSafeInteger(pageId)
    || pageId <= 0
    || !query
  ) {
    return json({ error: "A query, page, corpus, and valid relevance judgment are required." }, 400, "no-store");
  }

  const id = crypto.randomUUID();
  await env.DB.prepare(
    `INSERT INTO corpus_relevance_feedback (
       id, session_id, query_text, facet, corpus, page_id, entity, period,
       title, result_rank, judgment, route_json
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    id,
    normalizeText(payload.session_id, 120) || null,
    query,
    normalizeText(payload.facet, 160) || null,
    corpus,
    pageId,
    normalizeText(payload.entity, 40) || null,
    normalizeText(payload.period, 40) || null,
    normalizeText(payload.title, 300) || null,
    Number.isInteger(Number(payload.rank)) ? Math.max(1, Math.min(500, Number(payload.rank))) : null,
    judgment,
    normalizeText(JSON.stringify(payload.route || null), 4_000),
  ).run();

  return json({ ok: true, id }, 201, "no-store");
}

const CART_INCLUSION_MODES = new Set(["summary", "full"]);
const CART_SELECTION_SOURCES = new Set(["manual", "bundle", "reader", "similar"]);

function validSessionId(value: unknown): string | null {
  const sessionId = String(value || "").trim();
  return /^[a-zA-Z0-9._:-]{8,120}$/.test(sessionId) ? sessionId : null;
}

async function upsertResearchSession(
  env: Env,
  user: AuthenticatedUser,
  sessionId: string,
  session: Record<string, unknown>,
): Promise<boolean> {
  await env.DB.prepare(
    `INSERT INTO research_sessions (id, user_id, label, prompt, query_text, route_json, status)
     VALUES (?, ?, ?, ?, ?, ?, 'active')
     ON CONFLICT(id) DO UPDATE SET
       label = excluded.label,
       prompt = excluded.prompt,
       query_text = excluded.query_text,
       route_json = excluded.route_json,
       updated_at = CURRENT_TIMESTAMP
     WHERE research_sessions.user_id = excluded.user_id`,
  ).bind(
    sessionId,
    user.id,
    normalizeText(session.label, 240) || "Untitled analyst project",
    normalizeText(session.prompt, 4_000) || null,
    normalizeText(session.query_text, 2_000) || null,
    normalizeText(JSON.stringify(session.route || null), 8_000),
  ).run();
  const ownedSession = await env.DB.prepare(
    `SELECT id FROM research_sessions WHERE id = ? AND user_id = ?`,
  ).bind(sessionId, user.id).first<{ id: string }>();
  return Boolean(ownedSession);
}

async function handleCatalog(env: Env): Promise<Response> {
  const industries = await env.DB.prepare(
    `SELECT
       child.id,
       child.canonical_name AS name,
       child.parent_id,
       parent.canonical_name AS parent_name,
       child.sort_order
     FROM catalog_industries child
     LEFT JOIN catalog_industries parent ON parent.id = child.parent_id
     ORDER BY child.sort_order, child.canonical_name`,
  ).all<{
    id: string;
    name: string;
    parent_id: string | null;
    parent_name: string | null;
    sort_order: number;
  }>();
  return json({
    schema: CATALOG_SCHEMA,
    industries: industries.results || [],
  }, 200, "public, max-age=3600");
}

function parseJsonArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value !== "string" || !value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : [];
  } catch {
    return [];
  }
}

async function handleLibraryCollections(env: Env): Promise<Response> {
  const [rows, yearRows] = await Promise.all([env.DB.prepare(
    `SELECT collection_ref, publisher_id, publisher, display_name, description,
            why_tracked, coverage_note, cadence, status, landing_urls, industry_tags, signal_tags,
            document_count, local_document_count, r2_document_count,
            parsed_document_count, updated_at
     FROM authoritative_library_collections
     ORDER BY publisher, display_name`,
  ).all<Record<string, unknown>>(), env.DB.prepare(
    `SELECT collection_ref, year, COUNT(*) AS document_count,
            SUM(r2_available) AS r2_document_count
     FROM authoritative_library_documents
     GROUP BY collection_ref, year
     ORDER BY collection_ref, year DESC`,
  ).all<{ collection_ref: string; year: number | null; document_count: number; r2_document_count: number }>()]);
  const yearsByCollection = new Map<string, Array<Record<string, unknown>>>();
  for (const row of yearRows.results || []) {
    const years = yearsByCollection.get(row.collection_ref) || [];
    years.push({
      year: row.year,
      label: row.year == null ? "Undated" : String(row.year),
      document_count: Number(row.document_count || 0),
      r2_document_count: Number(row.r2_document_count || 0),
    });
    yearsByCollection.set(row.collection_ref, years);
  }
  return json({
    collections: (rows.results || []).map((row) => ({
      ...row,
      landing_urls: parseJsonArray(row.landing_urls),
      industry_tags: parseJsonArray(row.industry_tags),
      signal_tags: parseJsonArray(row.signal_tags),
      years: yearsByCollection.get(String(row.collection_ref)) || [],
    })),
  }, 200, "public, max-age=300");
}

async function handleLibraryDocuments(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const collectionRef = normalizeText(url.searchParams.get("collection_ref"), 160);
  if (!collectionRef) return json({ error: "collection_ref is required." }, 400);
  const query = normalizeText(url.searchParams.get("q"), 160);
  const requestedCoverage = normalizeText(url.searchParams.get("parse_coverage"), 20);
  const coverage = ["unparsed", "partial", "complete"].includes(requestedCoverage)
    ? requestedCoverage
    : "";
  const limit = Math.max(1, Math.min(100, Number(url.searchParams.get("limit")) || 50));
  const offset = Math.max(0, Math.min(100_000, Number(url.searchParams.get("offset")) || 0));
  const conditions = ["collection_ref = ?"];
  const bindings: unknown[] = [collectionRef];
  const requestedYear = normalizeText(url.searchParams.get("year"), 20);
  if (requestedYear === "undated") {
    conditions.push("year IS NULL");
  } else if (/^(?:19|20)\d{2}$/.test(requestedYear)) {
    conditions.push("year = ?");
    bindings.push(Number(requestedYear));
  }
  if (query) {
    conditions.push("title LIKE ? ESCAPE '\\'");
    bindings.push(`%${query.replace(/[\\%_]/g, "\\$&")}%`);
  }
  if (coverage) {
    conditions.push("parse_coverage = ?");
    bindings.push(coverage);
  }
  const where = conditions.join(" AND ");
  const count = await env.DB.prepare(
    `SELECT COUNT(*) AS count FROM authoritative_library_documents WHERE ${where}`,
  ).bind(...bindings).first<{ count: number }>();
  const rows = await env.DB.prepare(
    `SELECT document_id, collection_ref, source_title, title, document_type, published_date,
            document_date, year, year_confidence, source_url, byte_size,
            original_url, local_available, r2_available, page_count, parsed_pages,
            parse_coverage, first_seen_at, last_seen_at
     FROM authoritative_library_documents
     WHERE ${where}
     ORDER BY COALESCE(published_date, document_date, printf('%04d-01-01', year)) DESC,
              title
     LIMIT ? OFFSET ?`,
  ).bind(...bindings, limit, offset).all<Record<string, unknown>>();
  return json({
    documents: rows.results || [],
    total: Number(count?.count || 0),
    limit,
    offset,
  }, 200, "public, max-age=120");
}

async function handleCartGet(request: Request, env: Env, user: AuthenticatedUser): Promise<Response> {
  const sessionId = validSessionId(new URL(request.url).searchParams.get("session_id"));
  if (!sessionId) return json({ error: "A valid research session is required." }, 400, "no-store");

  const session = await env.DB.prepare(
    `SELECT id, label, prompt, query_text, route_json, status, created_at, updated_at
     FROM research_sessions
     WHERE id = ? AND user_id = ?`,
  ).bind(sessionId, user.id).first<Record<string, unknown>>();
  const lines = await env.DB.prepare(
    `SELECT
       corpus, page_id, catalog_item_id, document_id, evidence_facet, entity,
       industry_id, industry_name, period, title, source_url, thumbnail_url,
       page_number,
       asset_key, summary, snippet, bm25_score, retrieval_facets_json,
       catalog_json, inclusion_mode, selection_source, rank_at_selection,
       created_at, updated_at
     FROM research_cart_lines
     WHERE session_id = ?
       AND EXISTS (
         SELECT 1 FROM research_sessions
         WHERE research_sessions.id = research_cart_lines.session_id
           AND research_sessions.user_id = ?
       )
     ORDER BY created_at, corpus, page_id`,
  ).bind(sessionId, user.id).all<Record<string, unknown>>();

  return json({
    session,
    lines: (lines.results || []).map((row) => ({
      source: {
        id: Number(row.page_id),
        corpus: row.corpus,
        entity: String(row.entity || ""),
        industry: row.industry_name || null,
        industry_id: row.industry_id || null,
        source_name: String(row.entity || row.corpus || ""),
        title: String(row.title || "Untitled source"),
        period: String(row.period || ""),
        page_number: Number(row.page_number) || null,
        summary: String(row.summary || ""),
        snippet: String(row.snippet || ""),
        bm25_score: Number(row.bm25_score || 0),
        source_url: row.source_url || null,
        thumbnail_url: row.thumbnail_url || null,
        asset_key: row.asset_key || null,
        tags: [],
        quality_flags: [],
        catalog_item_id: row.catalog_item_id,
        document_id: row.document_id,
        retrieval_facets: parseTags(row.retrieval_facets_json),
        ...(row.catalog_json ? JSON.parse(String(row.catalog_json)) : {}),
      },
      inclusion_mode: row.inclusion_mode,
      selection_source: row.selection_source,
      rank_at_selection: row.rank_at_selection,
    })),
  }, 200, "no-store");
}

async function handleCartPost(request: Request, env: Env, user: AuthenticatedUser): Promise<Response> {
  let payload: Record<string, unknown>;
  try {
    payload = await request.json() as Record<string, unknown>;
  } catch {
    return json({ error: "Research cart payload must be valid JSON." }, 400, "no-store");
  }
  const sessionId = validSessionId(payload.session_id);
  if (!sessionId) return json({ error: "A valid research session is required." }, 400, "no-store");

  const session = payload.session && typeof payload.session === "object"
    ? payload.session as Record<string, unknown>
    : {};
  if (!await upsertResearchSession(env, user, sessionId, session)) {
    return json({ error: "Research session belongs to another user." }, 403, "no-store");
  }

  const candidates = Array.isArray(payload.lines)
    ? payload.lines
    : payload.line && typeof payload.line === "object"
      ? [payload.line]
      : [];
  let saved = 0;
  for (const candidate of candidates.slice(0, 24)) {
    if (!candidate || typeof candidate !== "object") continue;
    const line = candidate as Record<string, unknown>;
    const source = line.source && typeof line.source === "object"
      ? line.source as Record<string, unknown>
      : line;
    const corpus = String(source.corpus || "");
    const pageId = Number(source.id || source.page_id);
    if (!["filing", "announcement", "macro"].includes(corpus) || !Number.isSafeInteger(pageId) || pageId <= 0) continue;
    const inclusionMode = CART_INCLUSION_MODES.has(String(line.inclusion_mode || ""))
      ? String(line.inclusion_mode)
      : "full";
    const selectionSource = CART_SELECTION_SOURCES.has(String(line.selection_source || ""))
      ? String(line.selection_source)
      : "manual";
    const retrievalFacets = Array.isArray(source.retrieval_facets)
      ? source.retrieval_facets.map((value) => normalizeText(value, 160)).filter(Boolean).slice(0, 4)
      : [];
    const catalogJson = JSON.stringify({
      industry_path: normalizeText(source.industry_path, 240) || null,
      document_type: normalizeText(source.document_type, 80) || null,
      publisher: normalizeText(source.publisher, 160) || null,
      availability: source.availability && typeof source.availability === "object" ? source.availability : null,
    });

    await env.DB.prepare(
      `INSERT INTO research_cart_lines (
         session_id, corpus, page_id, catalog_item_id, document_id, evidence_facet,
         entity, industry_id, industry_name, period, title, source_url, thumbnail_url,
         page_number, asset_key, summary, snippet, bm25_score, retrieval_facets_json, catalog_json,
         inclusion_mode, selection_source, rank_at_selection
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(session_id, corpus, page_id) DO UPDATE SET
         evidence_facet = excluded.evidence_facet,
         entity = excluded.entity,
         industry_id = excluded.industry_id,
         industry_name = excluded.industry_name,
         period = excluded.period,
         page_number = excluded.page_number,
         title = excluded.title,
         source_url = excluded.source_url,
         thumbnail_url = excluded.thumbnail_url,
         asset_key = excluded.asset_key,
         summary = excluded.summary,
         snippet = excluded.snippet,
         bm25_score = excluded.bm25_score,
         retrieval_facets_json = excluded.retrieval_facets_json,
         catalog_json = excluded.catalog_json,
         inclusion_mode = excluded.inclusion_mode,
         selection_source = excluded.selection_source,
         rank_at_selection = excluded.rank_at_selection,
         updated_at = CURRENT_TIMESTAMP`,
    ).bind(
      sessionId,
      corpus,
      pageId,
      normalizeText(source.catalog_item_id, 180) || `${corpus}:${pageId}`,
      normalizeText(source.document_id, 360) || null,
      normalizeText(line.evidence_facet, 160) || retrievalFacets[0] || null,
      normalizeText(source.entity, 80) || null,
      normalizeText(source.industry_id, 80) || null,
      normalizeText(source.industry, 160) || null,
      normalizeText(source.period, 80) || null,
      normalizeText(source.title, 400) || "Untitled source",
      normalizeText(source.source_url, 1_000) || null,
      normalizeText(source.thumbnail_url, 1_000) || null,
      Number.isInteger(Number(source.page_number)) ? Number(source.page_number) : null,
      normalizeText(source.asset_key, 1_000) || null,
      normalizeText(source.summary, 2_500) || null,
      normalizeText(source.snippet, 1_200) || null,
      Number.isFinite(Number(source.bm25_score)) ? Number(source.bm25_score) : null,
      JSON.stringify(retrievalFacets),
      catalogJson,
      inclusionMode,
      selectionSource,
      Number.isInteger(Number(line.rank_at_selection)) ? Math.max(1, Number(line.rank_at_selection)) : null,
    ).run();
    saved += 1;
  }
  return json({ ok: true, session_id: sessionId, saved }, 200, "no-store");
}

async function handleHistoryGet(request: Request, env: Env, user: AuthenticatedUser): Promise<Response> {
  const sessionId = validSessionId(new URL(request.url).searchParams.get("session_id"));
  if (!sessionId) return json({ error: "A valid research session is required." }, 400, "no-store");
  const rows = await env.DB.prepare(
    `SELECT id, query_text, route_json, response_json, result_count, created_at
     FROM research_searches
     WHERE session_id = ?
       AND EXISTS (
         SELECT 1 FROM research_sessions
         WHERE research_sessions.id = research_searches.session_id
           AND research_sessions.user_id = ?
       )
     ORDER BY created_at DESC
     LIMIT 30`,
  ).bind(sessionId, user.id).all<Record<string, unknown>>();

  const searches = [];
  for (const row of rows.results || []) {
    try {
      searches.push({
        id: String(row.id),
        query_text: String(row.query_text || ""),
        route: JSON.parse(String(row.route_json || "null")),
        response: JSON.parse(String(row.response_json || "null")),
        result_count: Number(row.result_count || 0),
        created_at: String(row.created_at || ""),
      });
    } catch {
      // A malformed historical row must not prevent the rest of the trail loading.
    }
  }
  return json({ session_id: sessionId, searches }, 200, "no-store");
}

async function handleProjectsGet(env: Env, user: AuthenticatedUser): Promise<Response> {
  const rows = await env.DB.prepare(
    `SELECT
       sessions.id,
       sessions.label,
       sessions.query_text,
       sessions.status,
       sessions.created_at,
       sessions.updated_at,
       COALESCE((
         SELECT query_text
         FROM research_searches
         WHERE research_searches.session_id = sessions.id
         ORDER BY created_at DESC
         LIMIT 1
       ), sessions.query_text) AS last_query,
       (
         SELECT COUNT(*)
         FROM research_searches
         WHERE research_searches.session_id = sessions.id
       ) AS search_count,
       (
         SELECT COUNT(*)
         FROM research_cart_lines
         WHERE research_cart_lines.session_id = sessions.id
       ) AS context_count
     FROM research_sessions sessions
     WHERE sessions.user_id = ?
     ORDER BY sessions.updated_at DESC
     LIMIT 50`,
  ).bind(user.id).all<Record<string, unknown>>();
  return json({
    projects: (rows.results || []).map((row) => ({
      id: String(row.id),
      label: String(row.label || "Untitled analyst project"),
      last_query: String(row.last_query || ""),
      status: String(row.status || "active"),
      search_count: Number(row.search_count || 0),
      context_count: Number(row.context_count || 0),
      created_at: String(row.created_at || ""),
      updated_at: String(row.updated_at || ""),
    })),
  }, 200, "no-store");
}

async function handleHistoryPost(request: Request, env: Env, user: AuthenticatedUser): Promise<Response> {
  let payload: Record<string, unknown>;
  try {
    payload = await request.json() as Record<string, unknown>;
  } catch {
    return json({ error: "Search history payload must be valid JSON." }, 400, "no-store");
  }
  const sessionId = validSessionId(payload.session_id);
  const searchId = validSessionId(payload.id);
  const queryText = normalizeText(payload.query_text, 2_000);
  if (!sessionId || !searchId || !queryText) {
    return json({ error: "A session, search id, and query are required." }, 400, "no-store");
  }
  const session = payload.session && typeof payload.session === "object"
    ? payload.session as Record<string, unknown>
    : {};
  if (!await upsertResearchSession(env, user, sessionId, session)) {
    return json({ error: "Research session belongs to another user." }, 403, "no-store");
  }

  const routeJson = JSON.stringify(payload.route || null);
  const responseJson = JSON.stringify(payload.response || null);
  if (routeJson.length > 24_000 || responseJson.length > 160_000) {
    return json({ error: "Search history entry is too large." }, 413, "no-store");
  }
  const resultCount = Math.max(0, Math.min(500, Number(payload.result_count) || 0));
  await env.DB.prepare(
    `INSERT INTO research_searches (
       id, session_id, query_text, route_json, response_json, result_count
     ) VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(id) DO NOTHING`,
  ).bind(searchId, sessionId, queryText, routeJson, responseJson, resultCount).run();

  await env.DB.prepare(
    `DELETE FROM research_searches
     WHERE session_id = ?
       AND id NOT IN (
         SELECT id FROM research_searches
         WHERE session_id = ?
         ORDER BY created_at DESC
         LIMIT 30
       )`,
  ).bind(sessionId, sessionId).run();
  return json({ ok: true, id: searchId, session_id: sessionId }, 201, "no-store");
}

async function handleCartDelete(request: Request, env: Env, user: AuthenticatedUser): Promise<Response> {
  const url = new URL(request.url);
  const sessionId = validSessionId(url.searchParams.get("session_id"));
  if (!sessionId) return json({ error: "A valid research session is required." }, 400, "no-store");
  const corpus = String(url.searchParams.get("corpus") || "");
  const pageId = Number(url.searchParams.get("page_id"));
  if (["filing", "announcement", "macro"].includes(corpus) && Number.isSafeInteger(pageId) && pageId > 0) {
    await env.DB.prepare(
      `DELETE FROM research_cart_lines
       WHERE session_id = ? AND corpus = ? AND page_id = ?
         AND EXISTS (
           SELECT 1 FROM research_sessions
           WHERE research_sessions.id = research_cart_lines.session_id
             AND research_sessions.user_id = ?
         )`,
    ).bind(sessionId, corpus, pageId, user.id).run();
  } else {
    await env.DB.prepare(
      `DELETE FROM research_cart_lines
       WHERE session_id = ?
         AND EXISTS (
           SELECT 1 FROM research_sessions
           WHERE research_sessions.id = research_cart_lines.session_id
             AND research_sessions.user_id = ?
         )`,
    ).bind(sessionId, user.id).run();
  }
  return json({ ok: true, session_id: sessionId }, 200, "no-store");
}

const worker = {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS_HEADERS });

    const path = new URL(request.url).pathname;
    try {
      if (path === "/api/feedback") {
        const user = await authenticatedUser(request, env);
        if (!user) return json({ error: "Unauthorized." }, 401, "no-store");
        return request.method === "POST"
          ? await handleFeedback(request, env)
          : json({ error: "Method not allowed." }, 405);
      }
      if (path === "/api/cart") {
        const user = await authenticatedUser(request, env);
        if (!user) return json({ error: "Unauthorized." }, 401, "no-store");
        if (request.method === "GET") return await handleCartGet(request, env, user);
        if (request.method === "POST") return await handleCartPost(request, env, user);
        if (request.method === "DELETE") return await handleCartDelete(request, env, user);
        return json({ error: "Method not allowed." }, 405);
      }
      if (path === "/api/history") {
        const user = await authenticatedUser(request, env);
        if (!user) return json({ error: "Unauthorized." }, 401, "no-store");
        if (request.method === "GET") return await handleHistoryGet(request, env, user);
        if (request.method === "POST") return await handleHistoryPost(request, env, user);
        return json({ error: "Method not allowed." }, 405);
      }
      if (path === "/api/projects") {
        const user = await authenticatedUser(request, env);
        if (!user) return json({ error: "Unauthorized." }, 401, "no-store");
        return request.method === "GET"
          ? await handleProjectsGet(env, user)
          : json({ error: "Method not allowed." }, 405);
      }
      if (request.method !== "GET") return json({ error: "Method not allowed." }, 405);
      if (path === "/api/catalog") return await handleCatalog(env);
      if (path === "/api/library/collections") return await handleLibraryCollections(env);
      if (path === "/api/library/documents") return await handleLibraryDocuments(request, env);
      if (path === "/api/stats") return await handleStats(env);
      if (path === "/api/tickers") return await handleTickers(env);
      if (path === "/api/entities") return await handleEntities(env);
      if (path === "/api/search") return await handleSearch(request, env);
      if (path === "/api/similar") return await handleSimilar(request, env);
      if (path === "/api/document") return await handleDocument(request, env);
      if (path === "/health") return json({ ok: true, service: "psxgpt-corpus-api" });
      return json({ error: "Not found." }, 404);
    } catch (error) {
      return json({
        error: "Corpus query failed.",
        detail: error instanceof Error ? error.message : String(error),
      }, 500);
    }
  },
};

export default worker;
