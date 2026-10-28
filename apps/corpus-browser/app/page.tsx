"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import AuthGate from "./components/AuthGate";
import DocumentReader, { type ReaderDocumentDetail } from "./components/DocumentReader";
import { authenticatedFetch, useLedgerAuth } from "./lib/auth-client";
import { buildResultDisplaySections } from "./lib/result-presentation";
import {
  executeRetrievalPlan,
  type AppliedSearch,
  type CatalogFacet,
  type CorpusKind,
  type QualityFlag,
  type SearchResponse,
  type SearchResult,
} from "./lib/retrieval-execution";

type InclusionMode = "summary" | "full";
type PeriodPreset = "all" | "3y" | "5y" | "custom";

interface DocumentDetail {
  id?: number;
  corpus?: CorpusKind;
  text: string;
  search_text?: string;
  metadata?: Record<string, unknown>;
  source_url: string | null;
  thumbnail_url: string | null;
  asset: {
    key: string | null;
    status: "available" | "missing" | "external";
    bytes: number | null;
  };
  quality_flags: QualityFlag[];
}

interface SelectedSource extends SearchResult {
  loading: boolean;
  detail?: DocumentDetail;
  inclusionMode: InclusionMode;
}

interface CartResponse {
  session?: {
    id?: string;
    label?: string;
    prompt?: string;
    query_text?: string;
    route_json?: string;
  } | null;
  lines?: Array<{
    source: SearchResult;
    inclusion_mode: InclusionMode;
    selection_source: string;
    rank_at_selection?: number | null;
  }>;
}

interface SavedSearch {
  id: string;
  query_text: string;
  route: AppliedSearch;
  response: SearchResponse;
  result_count: number;
  created_at: string;
}

interface ResearchProject {
  id: string;
  label: string;
  last_query: string;
  status: string;
  search_count: number;
  context_count: number;
  created_at: string;
  updated_at: string;
}

interface StatsResponse {
  corpora: Array<{ corpus: CorpusKind; rows: number }>;
  total_documents: number;
  ranking: string;
  source_assets: string;
}

interface TickerResponse {
  tickers: string[];
  industries?: string[];
}

interface CompileResponse {
  answer: string;
  model: string;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
  error?: string;
}

const corpusLabels: Record<CorpusKind, string> = {
  filing: "Company filings",
  announcement: "PSX announcements",
  macro: "Economic & policy sources",
};

const corpusShortLabels: Record<CorpusKind, string> = {
  filing: "Filing",
  announcement: "Announcement",
  macro: "Macro",
};

const currentYear = new Date().getFullYear();
const MAX_SOURCE_CHARS = 12_000;
const MAX_COMPILE_SOURCES = 16;
const WORKSPACE_STORAGE_KEY = "ledger.workspace.v2";
const SESSION_STORAGE_KEY = "ledger.research-session.v1";
const DEFAULT_ANALYST_PROMPT =
  "Use the selected authoritative sources to prepare the requested analysis. Separate direct evidence from inference, reconcile inconsistencies, and cite every material claim.";
const examplePrompts = [
  "Build a financial model for UBL using the last 3 completed years",
  "Find HBL dividend announcements from the last 2 years",
  "Research Pakistan agriculture growth in the latest Economic Survey",
];

function sourceKey(source: Pick<SearchResult, "corpus" | "id">): string {
  return `${source.corpus}:${source.id}`;
}

function compactNumber(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function groupByEntity<T extends { entity: string }>(items: T[]): Array<[string, T[]]> {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const existing = groups.get(item.entity);
    if (existing) existing.push(item);
    else groups.set(item.entity, [item]);
  }
  return [...groups.entries()];
}

function highlightedParts(text: string): Array<{ text: string; marked: boolean }> {
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

function ResultExcerpt({ text }: { text: string }) {
  return (
    <>
      {highlightedParts(text).map((part, index) =>
        part.marked ? <mark key={index}>{part.text}</mark> : <span key={index}>{part.text}</span>,
      )}
    </>
  );
}

function cleanResultTitle(source: SearchResult): string {
  const title = (source.derived_heading || source.title)
    .replace(/^\s*\d+_+/, "")
    .replace(/\[\[|\]\]/g, "")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return title || source.title;
}

function cleanPreviewText(value: string): string {
  return value
    .replace(/\[\[|\]\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function pagePreview(source: SearchResult): { label: string; text: string } {
  const title = cleanResultTitle(source).toLocaleLowerCase();
  const snippet = source.snippet.trim();
  const cleanSnippet = cleanPreviewText(snippet);
  const summary = source.summary.trim();
  const cleanSummary = cleanPreviewText(summary);
  const summaryWords = summary
    .replace(/\[\[|\]\]/g, "")
    .replace(/[_#*`|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const generatedSummary = cleanPreviewText(source.page_index?.display_summary || "");
  if (generatedSummary.length >= 45 && generatedSummary.toLocaleLowerCase() !== title) {
    return { label: "What’s on this page", text: generatedSummary };
  }
  const snippetIsUseful = cleanSnippet.length >= 45
    && cleanSnippet.toLocaleLowerCase() !== title;
  const summaryIsUseful = summaryWords.length >= 55
    && summaryWords.toLocaleLowerCase() !== title;
  if (snippetIsUseful) return { label: "Relevant passage", text: snippet };
  if (summaryIsUseful) return { label: "Page contents", text: summary };
  if (cleanSnippet && cleanSnippet.toLocaleLowerCase() !== title) {
    return { label: "Relevant passage", text: snippet };
  }
  if (cleanSummary && cleanSummary.toLocaleLowerCase() !== title) {
    return { label: "Page contents", text: summary };
  }
  return { label: "Preview unavailable", text: "This result does not yet have a useful page-specific preview." };
}

function selectedContent(source: SelectedSource): string {
  const text = source.inclusionMode === "full" && source.detail?.text
    ? source.detail.text
    : source.summary || source.snippet;
  if (text.length <= MAX_SOURCE_CHARS) return text;
  return `${text.slice(0, MAX_SOURCE_CHARS)}\n\n[Source truncated at ${MAX_SOURCE_CHARS.toLocaleString()} characters]`;
}

function buildMarkdown(sessionLabel: string, prompt: string, sources: SelectedSource[]): string {
  const blocks = sources.map((source, index) => {
    const flags = source.detail?.quality_flags || source.quality_flags || [];
    return [
      `## Source ${index + 1} — ${source.entity}: ${source.title}`,
      `- Corpus: ${corpusLabels[source.corpus]}`,
      `- Period: ${source.period || "not stated"}`,
      `- Source: ${source.source_url || source.detail?.source_url || "not available"}`,
      `- Evidence facet: ${(source.retrieval_facets || []).join(", ") || "general evidence"}`,
      `- Inclusion: ${source.inclusionMode}`,
      `- Open deterministic flags: ${flags.length}`,
      "",
      selectedContent(source),
    ].join("\n");
  });
  return [
    `# ${sessionLabel || "Analyst context"}`,
    "",
    "## Analyst prompt",
    prompt,
    "",
    "## Human-curated authoritative sources",
    "",
    ...blocks,
  ].join("\n\n");
}

function resolveDates(preset: PeriodPreset, fromYear: string, toYear: string) {
  if (preset === "all") return { dateFrom: null, dateTo: null };
  if (preset === "3y") return { dateFrom: `${currentYear - 2}-01-01`, dateTo: `${currentYear}-12-31` };
  if (preset === "5y") return { dateFrom: `${currentYear - 4}-01-01`, dateTo: `${currentYear}-12-31` };
  const from = Number(fromYear);
  const to = Number(toYear);
  return {
    dateFrom: Number.isFinite(from) && from > 1900 ? `${from}-01-01` : null,
    dateTo: Number.isFinite(to) && to > 1900 ? `${to}-12-31` : null,
  };
}

function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

function refineSources(
  sources: SelectedSource[],
  mode: "dedupe" | "latest" | "cap",
): SelectedSource[] {
  const ranked = [...sources].sort((left, right) => right.bm25_score - left.bm25_score);
  if (mode === "dedupe") {
    const kept: SelectedSource[] = [];
    for (const source of ranked) {
      const adjacent = kept.some((other) =>
        other.corpus === source.corpus
        && other.entity === source.entity
        && other.period === source.period
        && other.page_number !== null
        && source.page_number !== null
        && Math.abs(other.page_number - source.page_number) <= 1
      );
      if (!adjacent) kept.push(source);
    }
    return kept;
  }
  if (mode === "latest") {
    const latest = new Map<string, string>();
    for (const source of sources) {
      const current = latest.get(source.entity);
      if (!current || source.period > current) latest.set(source.entity, source.period);
    }
    return sources.filter((source) => latest.get(source.entity) === source.period);
  }
  const counts = new Map<string, number>();
  return ranked.filter((source) => {
    const count = counts.get(source.entity) || 0;
    if (count >= 3) return false;
    counts.set(source.entity, count + 1);
    return true;
  });
}

export default function Home() {
  const auth = useLedgerAuth();
  const [sessionLabel, setSessionLabel] = useState("Untitled analyst project");
  const [query, setQuery] = useState("");
  const [tickerInput, setTickerInput] = useState("");
  const [tickerOptions, setTickerOptions] = useState<string[]>([]);
  const [selectedTickers, setSelectedTickers] = useState<string[]>([]);
  const [periodPreset, setPeriodPreset] = useState<PeriodPreset>("all");
  const [fromYear, setFromYear] = useState(String(currentYear - 2));
  const [toYear, setToYear] = useState(String(currentYear));
  const [corpora, setCorpora] = useState<CorpusKind[]>(["filing", "announcement", "macro"]);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [appliedSearch, setAppliedSearch] = useState<AppliedSearch | null>(null);
  const [search, setSearch] = useState<SearchResponse | null>(null);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchPhase, setSearchPhase] = useState<"routing" | "retrieving" | null>(null);
  const [searchError, setSearchError] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Record<string, SelectedSource>>({});
  const [catalogSessionId, setCatalogSessionId] = useState("");
  const [savedSearches, setSavedSearches] = useState<SavedSearch[]>([]);
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [cartSyncing, setCartSyncing] = useState(false);
  const [readingSource, setReadingSource] = useState<SearchResult | null>(null);
  const [workspaceHydrated, setWorkspaceHydrated] = useState(false);
  const [prompt, setPrompt] = useState(DEFAULT_ANALYST_PROMPT);
  const [sending, setSending] = useState(false);
  const [compiled, setCompiled] = useState<CompileResponse | null>(null);
  const [outputMode, setOutputMode] = useState<"reader" | "markdown">("reader");
  const [compileError, setCompileError] = useState("");
  const [toast, setToast] = useState("");
  const workspaceStorageKey = `${WORKSPACE_STORAGE_KEY}:${auth.user?.id || "anonymous"}`;
  const sessionStorageKey = `${SESSION_STORAGE_KEY}:${auth.user?.id || "anonymous"}`;

  useEffect(() => {
    if (!auth.user) return;
    Promise.all([
      authenticatedFetch("/api/stats").then((response) => {
        if (!response.ok) throw new Error("Stats unavailable");
        return response.json() as Promise<StatsResponse>;
      }),
      authenticatedFetch("/api/tickers").then((response) => {
        if (!response.ok) throw new Error("Tickers unavailable");
        return response.json() as Promise<TickerResponse>;
      }),
      authenticatedFetch("/api/projects").then((response) => {
        if (!response.ok) throw new Error("Projects unavailable");
        return response.json() as Promise<{ projects?: ResearchProject[] }>;
      }),
    ])
      .then(([statsData, tickerData, projectsData]) => {
        setStats(statsData);
        setTickerOptions(tickerData.tickers);
        setProjects(projectsData.projects || []);
      })
      .catch(() => setStats(null));
  }, [auth.user]);

  useEffect(() => {
    if (!auth.user) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        const requestedQuery = new URLSearchParams(window.location.search).get("q")?.trim() || "";
        const sessionId = window.localStorage.getItem(sessionStorageKey) || crypto.randomUUID();
        window.localStorage.setItem(sessionStorageKey, sessionId);
        if (!cancelled) setCatalogSessionId(sessionId);
        try {
          const raw = window.localStorage.getItem(workspaceStorageKey);
          if (raw) {
            const snapshot = JSON.parse(raw) as {
              sessionLabel?: string;
              query?: string;
              prompt?: string;
              appliedSearch?: AppliedSearch | null;
              search?: SearchResponse | null;
            };
            if (snapshot.sessionLabel) setSessionLabel(snapshot.sessionLabel);
            if (!requestedQuery && typeof snapshot.query === "string") setQuery(snapshot.query);
            if (snapshot.prompt) setPrompt(snapshot.prompt);
            if (!requestedQuery && snapshot.appliedSearch) setAppliedSearch(snapshot.appliedSearch);
            if (!requestedQuery && snapshot.search) setSearch(snapshot.search);
          }
          if (requestedQuery) setQuery(requestedQuery);

          const [cartResponse, historyResponse] = await Promise.all([
            authenticatedFetch(`/api/cart?session_id=${encodeURIComponent(sessionId)}`),
            authenticatedFetch(`/api/history?session_id=${encodeURIComponent(sessionId)}`),
          ]);
          const cart = await cartResponse.json() as CartResponse;
          const history = await historyResponse.json() as { searches?: SavedSearch[] };
          if (historyResponse.ok && !cancelled) {
            const searches = history.searches || [];
            setSavedSearches(searches);
            if (!raw && !requestedQuery && searches[0]) {
              setQuery(searches[0].query_text);
              setAppliedSearch(searches[0].route);
              setSearch(searches[0].response);
            }
          }
          if (!cartResponse.ok || cancelled) return;
          if (cart.session?.label) setSessionLabel(cart.session.label);
          if (cart.session?.prompt) setPrompt(cart.session.prompt);
          const restored = (cart.lines || []).slice(0, MAX_COMPILE_SOURCES).map((line) => ({
            ...line.source,
            loading: true,
            inclusionMode: line.inclusion_mode || "summary",
          } as SelectedSource));
          if (!restored.length) return;
          setSelected(Object.fromEntries(restored.map((source) => [sourceKey(source), source])));
          await Promise.all(restored.map(async (source) => {
            try {
              const response = await authenticatedFetch(`/api/document?corpus=${source.corpus}&id=${source.id}`);
              if (!response.ok) throw new Error("Document unavailable");
              const detail = await response.json() as DocumentDetail;
              if (cancelled) return;
              setSelected((current) => ({
                ...current,
                [sourceKey(source)]: {
                  ...current[sourceKey(source)],
                  loading: false,
                  detail,
                },
              }));
            } catch {
              if (cancelled) return;
              setSelected((current) => ({
                ...current,
                [sourceKey(source)]: {
                  ...current[sourceKey(source)],
                  loading: false,
                  inclusionMode: "summary",
                },
              }));
            }
          }));
        } catch {
          window.localStorage.removeItem(workspaceStorageKey);
        } finally {
          if (!cancelled) setWorkspaceHydrated(true);
        }
      })();
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [auth.user, sessionStorageKey, workspaceStorageKey]);

  useEffect(() => {
    if (!compiled) return;
    window.requestAnimationFrame(() => {
      document.getElementById("analyst-output")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [compiled]);

  const selectedSources = useMemo(() => Object.values(selected), [selected]);
  const selectedGroups = useMemo(() => groupByEntity(selectedSources), [selectedSources]);
  const resultSections = useMemo(
    () => buildResultDisplaySections(search, appliedSearch),
    [search, appliedSearch],
  );
  const markdown = useMemo(
    () => buildMarkdown(sessionLabel, prompt, selectedSources),
    [sessionLabel, prompt, selectedSources],
  );
  const contextCoverage = useMemo(() => selectedGroups.map(([entity, sources]) => ({
    entity,
    pages: sources.length,
    periods: [...new Set(sources.map((source) => source.period).filter(Boolean))].sort().reverse(),
  })), [selectedGroups]);
  const estimatedContextTokens = useMemo(() => estimateTokens(markdown), [markdown]);
  const apiPayload = useMemo(() => ({
    schema_version: "analyst-context.v2",
    research_session_id: catalogSessionId,
    session: sessionLabel,
    prompt,
    retrieval: appliedSearch,
    sources: selectedSources.map((source, index) => ({
      source_number: index + 1,
      id: source.id,
      catalog_item_id: source.catalog_item_id || sourceKey(source),
      document_id: source.document_id,
      corpus: source.corpus,
      entity: source.entity,
      industry_id: source.industry_id,
      industry: source.industry,
      publisher: source.publisher,
      document_type: source.document_type,
      title: source.title,
      period: source.period,
      source_url: source.source_url || source.detail?.source_url,
      asset_key: source.asset_key,
      r2_status: source.detail?.asset.status || "not verified",
      bm25_score: source.bm25_score,
      retrieval_lanes: source.retrieval_lanes || [],
      retrieval_facets: source.retrieval_facets || [],
      fusion_score: source.fusion_score,
      deterministic_flags: source.detail?.quality_flags || source.quality_flags,
      inclusion_mode: source.inclusionMode,
      search_text_summary: source.summary,
      content: selectedContent(source),
    })),
    analyst_context_markdown: markdown,
  }), [appliedSearch, catalogSessionId, markdown, prompt, selectedSources, sessionLabel]);

  useEffect(() => {
    if (!workspaceHydrated) return;
    const snapshot = {
      sessionLabel,
      query,
      prompt,
      appliedSearch,
      search,
    };
    try {
      window.localStorage.setItem(
        workspaceStorageKey,
        JSON.stringify(snapshot, (key, value) => key === "detail" || key === "loading" ? undefined : value),
      );
    } catch {
      // Persistence should never interrupt the active research session.
    }
  }, [appliedSearch, prompt, query, search, sessionLabel, workspaceHydrated, workspaceStorageKey]);

  useEffect(() => {
    if (!workspaceHydrated || !catalogSessionId) return;
    const timer = window.setTimeout(() => {
      void authenticatedFetch("/api/cart", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          session_id: catalogSessionId,
          session: {
            label: sessionLabel,
            prompt,
            query_text: appliedSearch?.naturalQuery || query,
            route: appliedSearch,
          },
          lines: [],
        }),
      }).catch(() => undefined);
    }, 450);
    return () => window.clearTimeout(timer);
  }, [appliedSearch, catalogSessionId, prompt, query, sessionLabel, workspaceHydrated]);

  async function executeSearch(interpreted: AppliedSearch) {
    setSelectedTickers(interpreted.tickers);
    setCorpora(interpreted.corpora);
    if (interpreted.dateFrom && interpreted.dateTo) {
      setFromYear(interpreted.dateFrom.slice(0, 4));
      setToYear(interpreted.dateTo.slice(0, 4));
      const span = Number(interpreted.dateTo.slice(0, 4)) - Number(interpreted.dateFrom.slice(0, 4)) + 1;
      setPeriodPreset(span === 3 ? "3y" : span === 5 ? "5y" : "custom");
    } else {
      setPeriodPreset("all");
    }
    if (sessionLabel === "Untitled analyst project") {
      setSessionLabel(interpreted.naturalQuery.slice(0, 88));
      setPrompt(interpreted.naturalQuery);
    }
    setAppliedSearch(interpreted);
    setCompiled(null);
    setReadingSource(null);
    setSearchPhase("retrieving");

    try {
      const completedSearch = await executeRetrievalPlan(interpreted, {
        fetcher: authenticatedFetch,
      });
      setSearch(completedSearch);
      await persistSearch(interpreted, completedSearch);
    } finally {
      setSearchPhase(null);
    }
  }

  async function persistSearch(interpreted: AppliedSearch, completedSearch: SearchResponse) {
    if (!catalogSessionId) return;
    const saved: SavedSearch = {
      id: crypto.randomUUID(),
      query_text: interpreted.naturalQuery,
      route: interpreted,
      response: {
        ...completedSearch,
        results: completedSearch.results.slice(0, 32),
      },
      result_count: completedSearch.result_count,
      created_at: new Date().toISOString(),
    };
    setSavedSearches((current) => [saved, ...current].slice(0, 30));
    try {
      const response = await authenticatedFetch("/api/history", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...saved,
          session_id: catalogSessionId,
          session: {
            label: sessionLabel === "Untitled analyst project"
              ? interpreted.naturalQuery.slice(0, 88)
              : sessionLabel,
            prompt: sessionLabel === "Untitled analyst project" ? interpreted.naturalQuery : prompt,
            query_text: interpreted.naturalQuery,
            route: interpreted,
          },
        }),
      });
      if (!response.ok) throw new Error("History sync failed");
      setProjects((current) => [{
        id: catalogSessionId,
        label: sessionLabel === "Untitled analyst project"
          ? interpreted.naturalQuery.slice(0, 88)
          : sessionLabel,
        last_query: interpreted.naturalQuery,
        status: "active",
        search_count: (current.find((project) => project.id === catalogSessionId)?.search_count || 0) + 1,
        context_count: Object.keys(selected).length,
        created_at: current.find((project) => project.id === catalogSessionId)?.created_at || new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }, ...current.filter((project) => project.id !== catalogSessionId)].slice(0, 50));
    } catch {
      setToast("Search completed; research trail sync is temporarily unavailable");
      window.setTimeout(() => setToast(""), 2200);
    }
  }

  function openSavedSearch(saved: SavedSearch) {
    setQuery(saved.query_text);
    setAppliedSearch(saved.route);
    setSearch(saved.response);
    setSelectedTickers(saved.route.tickers);
    setCorpora(saved.route.corpora);
    setCompiled(null);
    setReadingSource(null);
    setSearchError("");
  }

  async function openResearchProject(project: ResearchProject) {
    setSearching(true);
    setSearchError("");
    try {
      const [cartResponse, historyResponse] = await Promise.all([
        authenticatedFetch(`/api/cart?session_id=${encodeURIComponent(project.id)}`),
        authenticatedFetch(`/api/history?session_id=${encodeURIComponent(project.id)}`),
      ]);
      const cart = await cartResponse.json() as CartResponse;
      const history = await historyResponse.json() as { searches?: SavedSearch[] };
      if (!cartResponse.ok || !historyResponse.ok) throw new Error("Research project unavailable");

      const searches = history.searches || [];
      const latestSearch = searches[0];
      window.localStorage.setItem(sessionStorageKey, project.id);
      setCatalogSessionId(project.id);
      setSessionLabel(cart.session?.label || project.label);
      setPrompt(cart.session?.prompt || DEFAULT_ANALYST_PROMPT);
      setSavedSearches(searches);
      setQuery(latestSearch?.query_text || cart.session?.query_text || project.last_query);
      setAppliedSearch(latestSearch?.route || null);
      setSearch(latestSearch?.response || null);
      setCompiled(null);
      setReadingSource(null);
      setContextOpen(false);

      const restored = (cart.lines || []).slice(0, MAX_COMPILE_SOURCES).map((line) => ({
        ...line.source,
        loading: true,
        inclusionMode: line.inclusion_mode || "summary",
      } as SelectedSource));
      setSelected(Object.fromEntries(restored.map((source) => [sourceKey(source), source])));
      await Promise.all(restored.map(async (source) => {
        try {
          const response = await authenticatedFetch(`/api/document?corpus=${source.corpus}&id=${source.id}`);
          if (!response.ok) throw new Error("Document unavailable");
          const detail = await response.json() as DocumentDetail;
          setSelected((current) => ({
            ...current,
            [sourceKey(source)]: {
              ...current[sourceKey(source)],
              loading: false,
              detail,
            },
          }));
        } catch {
          setSelected((current) => ({
            ...current,
            [sourceKey(source)]: {
              ...current[sourceKey(source)],
              loading: false,
              inclusionMode: "summary",
            },
          }));
        }
      }));
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : "Research project unavailable");
    } finally {
      setSearching(false);
    }
  }

  async function searchFromText(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;
    setQuery(trimmed);
    setSearching(true);
    setSearchPhase("routing");
    setSearchError("");
    try {
      const routeResponse = await authenticatedFetch("/api/route", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          query: trimmed,
          clientYear: currentYear,
          context: appliedSearch ? {
            naturalQuery: appliedSearch.naturalQuery,
            tickers: appliedSearch.tickers,
            industries: appliedSearch.industries,
            years: appliedSearch.years,
            corpora: appliedSearch.corpora,
            filingCadence: appliedSearch.filingCadence,
            temporalMode: appliedSearch.temporalMode,
            latest: appliedSearch.latest,
            searchFacets: appliedSearch.searchFacets?.map((facet) => ({
              label: facet.label,
              lexicalQuery: facet.lexicalQuery,
            })),
          } : null,
        }),
      });
      const routed = await routeResponse.json() as AppliedSearch & { error?: string };
      if (!routeResponse.ok) throw new Error(routed.error || "The search router could not interpret this request.");
      await executeSearch(routed);
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : "Search failed");
    } finally {
      setSearching(false);
      setSearchPhase(null);
    }
  }

  function runSearch(event: FormEvent) {
    event.preventDefault();
    void searchFromText(query);
  }

  async function applyManualScope() {
    if (!appliedSearch) return;
    const dates = resolveDates(periodPreset, fromYear, toYear);
    const from = dates.dateFrom ? Number(dates.dateFrom.slice(0, 4)) : null;
    const to = dates.dateTo ? Number(dates.dateTo.slice(0, 4)) : null;
    const years = from && to
      ? Array.from(
          { length: Math.max(0, Math.min(10, to - from + 1)) },
          (_, index) => from + index,
        )
      : [];
    const manual: AppliedSearch = {
      ...appliedSearch,
      tickers: [...selectedTickers],
      entities: appliedSearch.entities?.filter((entity) => selectedTickers.includes(entity.ticker)),
      industries: [...appliedSearch.industries],
      years,
      dateFrom: dates.dateFrom,
      dateTo: dates.dateTo,
      corpora: [...corpora],
      routedBy: "manual",
    };
    setSearching(true);
    setSearchError("");
    try {
      await executeSearch(manual);
      setShowAdvanced(false);
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : "Search failed");
    } finally {
      setSearching(false);
      setSearchPhase(null);
    }
  }

  async function applyCatalogFacet(facet: CatalogFacet, value: string) {
    if (!appliedSearch || facet.key === "publisher") return;
    const next: AppliedSearch = {
      ...appliedSearch,
      entities: appliedSearch.entities,
      tickers: [...appliedSearch.tickers],
      industries: [...(appliedSearch.industries || [])],
      years: [...appliedSearch.years],
      corpora: [...appliedSearch.corpora],
      routedBy: "manual",
    };
    if (facet.key === "entity") {
      next.tickers = [value];
      next.entities = appliedSearch.entities?.filter((entity) => entity.ticker === value);
      next.industries = [];
    } else if (facet.key === "industry") {
      next.tickers = [];
      next.entities = [];
      next.industries = [value];
    } else if (facet.key === "period") {
      const year = Number(value);
      if (!Number.isInteger(year)) return;
      next.years = [year];
      next.dateFrom = `${year}-01-01`;
      next.dateTo = `${year}-12-31`;
    } else if (facet.key === "corpus" && ["filing", "announcement", "macro"].includes(value)) {
      next.corpora = [value as CorpusKind];
    }
    setSearching(true);
    setSearchError("");
    try {
      await executeSearch(next);
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : "Catalog refinement failed");
    } finally {
      setSearching(false);
      setSearchPhase(null);
    }
  }

  function catalogFacetActive(facet: CatalogFacet, value: string): boolean {
    if (!appliedSearch) return false;
    if (facet.key === "entity") return appliedSearch.tickers.includes(value);
    if (facet.key === "industry") return (appliedSearch.industries || []).includes(value);
    if (facet.key === "period") return appliedSearch.years.includes(Number(value));
    if (facet.key === "corpus") return appliedSearch.corpora.length === 1 && appliedSearch.corpora[0] === value;
    return false;
  }

  function addTicker() {
    const ticker = tickerInput.trim().toUpperCase();
    if (!ticker || selectedTickers.includes(ticker)) return;
    setSelectedTickers((current) => [...current, ticker]);
    setTickerInput("");
  }

  function toggleCorpus(corpus: CorpusKind) {
    setCorpora((current) => {
      if (current.includes(corpus)) return current.length === 1 ? current : current.filter((value) => value !== corpus);
      return [...current, corpus];
    });
  }

  async function persistCartLine(
    source: SearchResult,
    inclusionMode: InclusionMode,
    selectionSource: "manual" | "bundle" | "reader" | "similar" = "manual",
    rankAtSelection?: number,
  ) {
    if (!catalogSessionId) return;
    setCartSyncing(true);
    try {
      const response = await authenticatedFetch("/api/cart", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          session_id: catalogSessionId,
          session: {
            label: sessionLabel,
            prompt,
            query_text: appliedSearch?.naturalQuery || query,
            route: appliedSearch,
          },
          line: {
            source,
            evidence_facet: source.retrieval_facets?.[0] || null,
            inclusion_mode: inclusionMode,
            selection_source: selectionSource,
            rank_at_selection: rankAtSelection,
          },
        }),
      });
      if (!response.ok) throw new Error("Cart sync failed");
    } catch {
      setToast("Evidence selected; durable cart sync is temporarily unavailable");
      window.setTimeout(() => setToast(""), 2200);
    } finally {
      setCartSyncing(false);
    }
  }

  async function removeCartLine(source: Pick<SearchResult, "corpus" | "id">) {
    if (!catalogSessionId) return;
    await authenticatedFetch(
      `/api/cart?session_id=${encodeURIComponent(catalogSessionId)}&corpus=${source.corpus}&page_id=${source.id}`,
      { method: "DELETE" },
    ).catch(() => undefined);
  }

  async function clearResearchCart() {
    setSelected({});
    if (!catalogSessionId) return;
    await authenticatedFetch(`/api/cart?session_id=${encodeURIComponent(catalogSessionId)}`, { method: "DELETE" })
      .catch(() => undefined);
  }

  async function replaceResearchCart(sources: SelectedSource[]) {
    if (!catalogSessionId) return;
    await authenticatedFetch(`/api/cart?session_id=${encodeURIComponent(catalogSessionId)}`, { method: "DELETE" });
    if (!sources.length) return;
    await authenticatedFetch("/api/cart", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        session_id: catalogSessionId,
        session: {
          label: sessionLabel,
          prompt,
          query_text: appliedSearch?.naturalQuery || query,
          route: appliedSearch,
        },
        lines: sources.map((source) => ({
          source,
          evidence_facet: source.retrieval_facets?.[0] || null,
          inclusion_mode: source.inclusionMode,
          selection_source: "manual",
        })),
      }),
    });
  }

  async function toggleSource(
    source: SearchResult,
    selectionSource: "manual" | "bundle" | "reader" | "similar" = "manual",
    rankAtSelection?: number,
  ) {
    const key = sourceKey(source);
    if (selected[key]) {
      setSelected((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      void removeCartLine(source);
      return;
    }

    setSelected((current) => ({
      ...current,
      [key]: { ...source, loading: true, inclusionMode: "full" },
    }));
    try {
      const response = await authenticatedFetch(`/api/document?corpus=${source.corpus}&id=${source.id}`);
      const detail = await response.json() as DocumentDetail;
      if (!response.ok) throw new Error("Source could not be loaded");
      setSelected((current) => ({
        ...current,
        [key]: { ...current[key], loading: false, detail },
      }));
      void persistCartLine(source, "full", selectionSource, rankAtSelection);
    } catch {
      setSelected((current) => ({
        ...current,
        [key]: { ...current[key], loading: false, inclusionMode: "summary" },
      }));
      void persistCartLine(source, "summary", selectionSource, rankAtSelection);
    }
  }

  function toggleReaderPage(
    origin: SearchResult,
    id: number,
    detail: ReaderDocumentDetail,
    pageNumber: number | null,
  ) {
    const key = `${origin.corpus}:${id}`;
    if (selected[key]) {
      setSelected((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      void removeCartLine({ corpus: origin.corpus, id });
      return;
    }
    const source: SelectedSource = {
      ...origin,
      id,
      title: pageNumber
        ? `${origin.title.replace(/ — page \d+$/i, "")} — page ${pageNumber}`
        : origin.title,
      page_number: pageNumber,
      summary: detail.search_text || origin.summary,
      source_url: detail.source_url || origin.source_url,
      thumbnail_url: detail.thumbnail_url || origin.thumbnail_url,
      asset_key: detail.asset.key || origin.asset_key,
      quality_flags: detail.quality_flags,
      bm25_score: id === origin.id ? origin.bm25_score : 0,
      loading: false,
      detail: detail as DocumentDetail,
      inclusionMode: "full",
    };
    setSelected((current) => ({ ...current, [key]: source }));
    void persistCartLine(source, "full", "reader");
  }

  function applyContextRefinement(mode: "dedupe" | "latest" | "cap") {
    const refined = refineSources(selectedSources, mode);
    if (refined.length === selectedSources.length) {
      setToast("The evidence set is already tidy");
      window.setTimeout(() => setToast(""), 1800);
      return;
    }
    setSelected(Object.fromEntries(refined.map((source) => [sourceKey(source), source])));
    void replaceResearchCart(refined).catch(() => {
      setToast("Evidence refined; durable cart sync is temporarily unavailable");
    });
    setToast(`${selectedSources.length - refined.length} redundant source${selectedSources.length - refined.length === 1 ? "" : "s"} removed`);
    window.setTimeout(() => setToast(""), 1800);
  }

  function updateMode(source: SelectedSource, inclusionMode: InclusionMode) {
    const key = sourceKey(source);
    setSelected((current) => ({
      ...current,
      [key]: { ...current[key], inclusionMode },
    }));
    void persistCartLine(source, inclusionMode);
  }

  async function copyText(text: string, message: string) {
    await navigator.clipboard.writeText(text);
    setToast(message);
    window.setTimeout(() => setToast(""), 1800);
  }

  function downloadMarkdown() {
    if (!compiled?.answer) return;
    const blob = new Blob([compiled.answer], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${sessionLabel.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase() || "ledger-analysis"}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function compileAnswer() {
    if (!selectedSources.length) return;
    setSending(true);
    setCompileError("");
    setCompiled(null);
    setOutputMode("reader");
    try {
      const response = await authenticatedFetch("/api/compile", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(apiPayload),
      });
      const data = await response.json() as CompileResponse;
      if (!response.ok) throw new Error(data.error || "Answer compilation failed");
      setCompiled(data);
      setReadingSource(null);
    } catch (error) {
      setCompileError(error instanceof Error ? error.message : "Answer compilation failed");
    } finally {
      setSending(false);
    }
  }

  function resetWorkspace() {
    const nextSessionId = crypto.randomUUID();
    setCatalogSessionId(nextSessionId);
    window.localStorage.setItem(sessionStorageKey, nextSessionId);
    setQuery("");
    setSearch(null);
    setAppliedSearch(null);
    setSavedSearches([]);
    setSelected({});
    setReadingSource(null);
    setCompiled(null);
    setSearchError("");
    setSessionLabel("Untitled analyst project");
    setPrompt(DEFAULT_ANALYST_PROMPT);
    setShowAdvanced(false);
    setContextOpen(false);
    window.localStorage.removeItem(workspaceStorageKey);
  }

  if (auth.loading || !auth.user) {
    return (
      <AuthGate
        loading={auth.loading}
        configured={auth.configured}
        error={auth.error}
        onGoogle={auth.signInWithGoogle}
        onMagicLink={auth.sendMagicLink}
      />
    );
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <button className="brand-lockup" type="button" onClick={resetWorkspace} aria-label="Start new Ledger research">
          <span className="brand-mark">L</span>
          <span><strong>Ledger</strong><small>Analyst workspace</small></span>
        </button>
        <div className="header-actions">
          <Link className="library-nav-link" href="/library">PDF library</Link>
          <span className="corpus-status"><i /> Authoritative corpus live</span>
          <span className="account-chip" title={auth.user.email || "Signed in"}>
            {(auth.user.email || "U").slice(0, 1).toUpperCase()}
            <small>{auth.user.email}</small>
          </span>
          <button type="button" className="sign-out" onClick={() => void auth.signOut()}>Sign out</button>
          <button type="button" className="new-research" onClick={resetWorkspace}>New research</button>
        </div>
      </header>

      <div className={`workspace ${search ? "has-results" : ""}`}>
        <section className="research-column">
          <section className={`query-stage ${search ? "compact" : ""}`}>
            {!search && (
              <div className="hero-copy">
                <span className="kicker">Research with source-level control</span>
                <h1>What are you building?</h1>
                <p>
                  Describe the analyst outcome. Ledger resolves the scope, finds the strongest authoritative pages,
                  and keeps you in control of the evidence.
                </p>
              </div>
            )}
            <form className="intent-composer" onSubmit={runSearch}>
              <div className="composer-icon" aria-hidden="true">⌕</div>
              <textarea
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void searchFromText(query);
                  }
                }}
                rows={search ? 1 : 2}
                placeholder="I want to build a financial model for UBL using the last 3 completed years…"
                aria-label="Describe your research goal"
              />
              <button type="submit" disabled={!query.trim() || searching}>
                {searchPhase === "routing"
                  ? "Understanding"
                  : searchPhase === "retrieving"
                    ? "Finding evidence"
                    : search
                      ? "Search again"
                      : "Find evidence"}
                {!searching && <span>→</span>}
              </button>
            </form>
            {savedSearches.length > 0 && (
              <nav className="research-trail" aria-label="Saved searches in this research project">
                <span>Research trail</span>
                <div>
                  {savedSearches.slice(0, 8).map((saved) => (
                    <button
                      type="button"
                      className={appliedSearch?.naturalQuery === saved.query_text ? "active" : ""}
                      key={saved.id}
                      onClick={() => openSavedSearch(saved)}
                      title={saved.query_text}
                    >
                      <strong>{saved.query_text}</strong>
                      <small>{saved.result_count} pages</small>
                    </button>
                  ))}
                </div>
              </nav>
            )}
            {!search && (
              <section className="past-research" aria-label="Past research projects">
                <div className="past-research-heading">
                  <div>
                    <span className="section-label">Past research</span>
                    <strong>Continue where you left off</strong>
                  </div>
                  <small>{projects.length ? `${projects.length} saved` : "No saved research yet"}</small>
                </div>
                {projects.length > 0 && (
                  <div className="past-research-list">
                    {projects.slice(0, 8).map((project) => (
                      <button
                        type="button"
                        key={project.id}
                        onClick={() => void openResearchProject(project)}
                        disabled={searching}
                      >
                        <span>
                          <strong>{project.label || project.last_query}</strong>
                          <small>{project.last_query}</small>
                        </span>
                        <em>
                          {project.search_count} {project.search_count === 1 ? "search" : "searches"}
                          {project.context_count > 0 ? ` · ${project.context_count} pages` : ""}
                        </em>
                        <b aria-hidden="true">→</b>
                      </button>
                    ))}
                  </div>
                )}
              </section>
            )}
            {!search && (
              <div className="prompt-suggestions">
                <span>Try a workflow</span>
                {examplePrompts.map((example) => (
                  <button type="button" key={example} onClick={() => void searchFromText(example)}>
                    {example}
                  </button>
                ))}
              </div>
            )}
            {!search && stats && (
              <div className="corpus-proof">
                <strong>{compactNumber(stats.total_documents)}</strong> searchable source pages
                <span />
                filings, announcements, and economic publications
              </div>
            )}
          </section>

          {appliedSearch && (
            <section className="research-plan">
              <div className="plan-main">
                <div className="plan-heading">
                  <span className="section-label">Search scope</span>
                  <strong>{appliedSearch.interpretation || "Evidence search"}</strong>
                </div>
                <div className="plan-chips">
                  {(appliedSearch.entities?.length
                    ? appliedSearch.entities
                    : appliedSearch.tickers.map((ticker) => ({ ticker, company_name: "", mention: ticker, confidence: 1 }))
                  ).map((entity) => (
                    <span className="plan-chip strong" key={entity.ticker}>
                      {entity.ticker}
                    </span>
                  ))}
                  {appliedSearch.industries?.map((industry) => (
                    <span className="plan-chip accent" key={industry}>
                      Industry · {industry}
                    </span>
                  ))}
                  <span className="plan-chip">
                    {appliedSearch.dateFrom && appliedSearch.dateTo
                      ? `${appliedSearch.dateFrom.slice(0, 4)}–${appliedSearch.dateTo.slice(0, 4)}`
                      : "All periods"}
                  </span>
                  {appliedSearch.corpora.map((corpus) => (
                    <span className="plan-chip" key={corpus}>{corpusLabels[corpus]}</span>
                  ))}
                  {appliedSearch.queryPack === "financial_statements" && (
                    <span className="plan-chip accent">Core statement pages</span>
                  )}
                </div>
                {appliedSearch.evidenceNeeds && appliedSearch.evidenceNeeds.length > 0 && (
                  <div className="evidence-needs" aria-label="Evidence the search will look for">
                    <span>Looking for</span>
                    {appliedSearch.evidenceNeeds.map((need) => <em key={need}>{need}</em>)}
                  </div>
                )}
              </div>
              <div className="plan-actions">
                <span><i /> Inferred</span>
                <button type="button" onClick={() => setShowAdvanced((current) => !current)}>
                  {showAdvanced ? "Close" : "Edit scope"}
                </button>
              </div>
              {appliedSearch.unresolvedEntities && appliedSearch.unresolvedEntities.length > 0 && (
                <div className="route-warning">
                  Could not confidently resolve {appliedSearch.unresolvedEntities.join(", ")}. Use an exact ticker below.
                </div>
              )}
            </section>
          )}

          {search && (search.result_facets?.length || 0) > 0 && (
            <details className="catalog-refinements">
              <summary>
                <div>
                  <span className="section-label">Refine results</span>
                  <strong>Company, industry, period, or source type</strong>
                </div>
                <span className="details-action">Filters</span>
              </summary>
              <div className="catalog-facet-list">
                {search.result_facets!
                  .filter((facet) => facet.key !== "publisher")
                  .map((facet) => (
                    <div className="catalog-facet" key={facet.key}>
                      <span>{facet.label}</span>
                      <div>
                        {facet.values.slice(0, 8).map((item) => (
                          <button
                            type="button"
                            className={catalogFacetActive(facet, item.value) ? "active" : ""}
                            key={item.value}
                            disabled={searching}
                            onClick={() => void applyCatalogFacet(facet, item.value)}
                          >
                            {item.label} <em>{item.count}</em>
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
              </div>
            </details>
          )}

          {showAdvanced && (
            <section className="scope-editor" aria-label="Adjust search scope">
              <div className="scope-field">
                <label htmlFor="ticker-filter">Companies</label>
                <div className="ticker-entry">
                  <input
                    id="ticker-filter"
                    list="ticker-options"
                    value={tickerInput}
                    onChange={(event) => setTickerInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        addTicker();
                      }
                    }}
                    placeholder="Add ticker"
                  />
                  <datalist id="ticker-options">
                    {tickerOptions.map((ticker) => <option value={ticker} key={ticker} />)}
                  </datalist>
                  <button type="button" onClick={addTicker}>Add</button>
                </div>
                <div className="scope-tokens">
                  {selectedTickers.map((ticker) => (
                    <button
                      type="button"
                      key={ticker}
                      onClick={() => setSelectedTickers((current) => current.filter((value) => value !== ticker))}
                    >
                      {ticker} ×
                    </button>
                  ))}
                </div>
              </div>
              <div className="scope-field">
                <label htmlFor="period-filter">Period</label>
                <select
                  id="period-filter"
                  value={periodPreset}
                  onChange={(event) => setPeriodPreset(event.target.value as PeriodPreset)}
                >
                  <option value="all">All periods</option>
                  <option value="3y">Last 3 years</option>
                  <option value="5y">Last 5 years</option>
                  <option value="custom">Custom years</option>
                </select>
                {periodPreset === "custom" && (
                  <div className="year-range">
                    <input aria-label="From year" type="number" value={fromYear} onChange={(event) => setFromYear(event.target.value)} />
                    <span>to</span>
                    <input aria-label="To year" type="number" value={toYear} onChange={(event) => setToYear(event.target.value)} />
                  </div>
                )}
              </div>
              <div className="scope-field">
                <label>Sources</label>
                <div className="corpus-toggles">
                  {(Object.keys(corpusLabels) as CorpusKind[]).map((corpus) => (
                    <button
                      type="button"
                      className={corpora.includes(corpus) ? "active" : ""}
                      key={corpus}
                      onClick={() => toggleCorpus(corpus)}
                    >
                      {corpusShortLabels[corpus]}
                    </button>
                  ))}
                </div>
              </div>
              <button className="apply-scope" type="button" onClick={() => void applyManualScope()} disabled={searching}>
                Apply changes
              </button>
            </section>
          )}

          {searchError && <div className="error-state">{searchError}</div>}

          {readingSource && (
            <DocumentReader
              key={sourceKey(readingSource)}
              origin={readingSource}
              isSelected={(corpus, id) => Boolean(selected[`${corpus}:${id}`])}
              onToggle={toggleReaderPage}
              onClose={() => setReadingSource(null)}
            />
          )}

          {compiled && !readingSource && (
            <section className="report-reader" id="analyst-output">
              <header className="report-header">
                <div>
                  <span className="section-label">Analyst output</span>
                  <h2>{sessionLabel}</h2>
                  <p>
                    Compiled from {selectedSources.length} selected authoritative sources
                    <span> · </span>{compiled.model}
                  </p>
                </div>
                <div className="report-status"><i /> Evidence-grounded</div>
              </header>
              <div className="report-toolbar">
                <div className="view-toggle" aria-label="Output view">
                  <button
                    type="button"
                    className={outputMode === "reader" ? "active" : ""}
                    onClick={() => setOutputMode("reader")}
                  >
                    Reader
                  </button>
                  <button
                    type="button"
                    className={outputMode === "markdown" ? "active" : ""}
                    onClick={() => setOutputMode("markdown")}
                  >
                    Markdown
                  </button>
                </div>
                <div className="report-actions">
                  <button type="button" onClick={() => void copyText(compiled.answer, "Answer copied")}>
                    Copy
                  </button>
                  <button type="button" onClick={downloadMarkdown}>
                    Download .md
                  </button>
                </div>
              </div>
              {outputMode === "reader" ? (
                <article className="markdown-reader">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{compiled.answer}</ReactMarkdown>
                </article>
              ) : (
                <pre className="raw-markdown">{compiled.answer}</pre>
              )}
              <footer className="report-footer">
                <span>Review citations against the selected source pages before relying on the analysis.</span>
                {compiled.usage?.total_tokens && <span>{compiled.usage.total_tokens.toLocaleString()} model tokens</span>}
              </footer>
            </section>
          )}

          {search && !searchError && !readingSource && (
            <section className="results-section">
              <header className="results-header">
                <div>
                  <span className="section-label">Search results</span>
                  <h2>Ranked pages from authoritative sources</h2>
                  <p>
                    Review the ranked pages in each source group. Open the Markdown or PDF, then add only useful pages to your context.
                  </p>
                </div>
              </header>

              {(search.coverage_gaps?.length || 0) > 0 && (
                <details className="coverage-note">
                  <summary>{search.coverage_gaps!.length} coverage gaps to review</summary>
                  <p>
                    {search.coverage_gaps!.map((gap) =>
                      `${gap.ticker} ${gap.year}: ${gap.missing_sections.join(", ")}`,
                    ).join(" · ")}
                  </p>
                </details>
              )}

              {resultSections.length === 0 ? (
                <div className="empty-state">
                  <strong>No strong evidence found</strong>
                  <span>Adjust the company, period, or wording and search again.</span>
                </div>
              ) : resultSections.map((section) => {
                return (
                  <section className="facet-result-section" key={section.key}>
                    <header className="facet-result-header">
                      <span>{section.eyebrow}</span>
                      <div>
                        <h3>{section.title}</h3>
                        <small>{section.sources.length} results · newest first</small>
                      </div>
                    </header>
                    {section.groups.map((group) => {
                      const collapseKey = `${section.key}:${group.key}`;
                      const isCollapsed = collapsed.has(collapseKey);
                      const selectedCount = group.sources.filter(
                        (source) => selected[sourceKey(source)],
                      ).length;
                      return (
                        <section className="entity-section" key={collapseKey}>
                          <button
                            className="entity-heading"
                            type="button"
                            onClick={() => setCollapsed((current) => {
                              const next = new Set(current);
                              if (next.has(collapseKey)) next.delete(collapseKey);
                              else next.add(collapseKey);
                              return next;
                            })}
                          >
                            <span className="entity-avatar">{group.label.slice(0, 2)}</span>
                            <span>
                              <strong>{group.label}</strong>
                              <small>{group.sources.length} results</small>
                            </span>
                            {selectedCount > 0 && <em>{selectedCount} selected</em>}
                            <b>{isCollapsed ? "＋" : "−"}</b>
                          </button>
                          {!isCollapsed && (
                            <div className="result-grid">
                              {group.sources.map((source, localRank) => {
                                const key = sourceKey(source);
                                const isSelected = Boolean(selected[key]);
                                const flags = source.quality_flags || [];
                                const preview = pagePreview(source);
                                return (
                                  <article className={`evidence-card ${isSelected ? "selected" : ""}`} key={key}>
                                    <div className="evidence-topline">
                                      <div className="evidence-labels">
                                        <span className={`corpus-pill ${source.corpus}`}>{corpusShortLabels[source.corpus]}</span>
                                        {source.industry && <span className="industry-pill">{source.industry}</span>}
                                        {(source.page_signals?.tableRows || 0) >= 3 && <span className="page-shape-pill">Table</span>}
                                        {source.page_index?.parse_status === "incomplete" && (
                                          <span className="page-shape-pill caution">Partial extract</span>
                                        )}
                                      </div>
                                      <span>{source.period || "Undated"}{source.page_number ? ` · Page ${source.page_number}` : ""}</span>
                                    </div>
                                    <h3>{cleanResultTitle(source)}</h3>
                                    <div className="page-preview">
                                      <span>{preview.label}</span>
                                      <p><ResultExcerpt text={preview.text} /></p>
                                    </div>
                                    <div className="evidence-footer">
                                      {flags.length > 0 && (
                                        <span className="warning-count">{flags.length} data checks</span>
                                      )}
                                      <div className="evidence-actions">
                                        <button
                                          type="button"
                                          className="read-source"
                                          onClick={() => setReadingSource(source)}
                                        >
                                          Read page
                                        </button>
                                        {source.source_url && (
                                          <a href={source.source_url} target="_blank" rel="noreferrer">
                                            {source.corpus === "announcement" ? "View announcement ↗" : "View PDF page ↗"}
                                          </a>
                                        )}
                                        <button
                                          type="button"
                                          className={isSelected ? "selected" : ""}
                                          onClick={() => void toggleSource(
                                            source,
                                            "manual",
                                            localRank + 1,
                                          )}
                                        >
                                          {isSelected ? "✓ In context" : "Add page to context"}
                                        </button>
                                      </div>
                                    </div>
                                  </article>
                                );
                              })}
                            </div>
                          )}
                        </section>
                      );
                    })}
                  </section>
                );
              })}

              <div className="search-disclosure">
                <span>Search details</span>
                <p>
                  {appliedSearch?.queryPack === "financial_statements"
                    ? "Core statement query pack · BM25 candidates · heading recovery · navigation suppressed"
                    : `Summary, page text, and markdown-heading BM25 lanes · reciprocal-rank fusion · table-aware reranking${appliedSearch?.industries?.length ? ` · industry scope: ${appliedSearch.industries.join(", ")}` : ""}`}
                </p>
                {search.field_queries && appliedSearch?.queryPack !== "financial_statements" && (
                  <div className="field-query-list">
                    <span><b>Page key</b> “{search.field_queries.key}”</span>
                    <span><b>Summary</b> “{search.field_queries.summary}”</span>
                    <span><b>Page text</b> “{search.field_queries.text}”</span>
                    <span><b>Heading</b> “{search.field_queries.heading}”</span>
                  </div>
                )}
                {(search.search_facets?.length || 0) > 1 && (
                  <div className="facet-query-list">
                    {search.search_facets!.map((facet) => (
                      <span key={facet.label}>
                        <b>{facet.label}</b> {facet.lexicalQuery}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </section>
          )}
        </section>

        <aside className={`context-rail ${contextOpen ? "open" : ""}`}>
          <button className="mobile-close" type="button" onClick={() => setContextOpen(false)} aria-label="Close context">
            ×
          </button>
          <header className="context-header">
            <span className="section-label">Selected pages</span>
            <div>
              <h2>{selectedSources.length ? `${selectedSources.length} sources selected` : "Build your evidence set"}</h2>
              {selectedSources.length > 0 && (
                <button type="button" onClick={() => void clearResearchCart()}>Clear</button>
              )}
            </div>
            <p>
              {selectedSources.length
                ? "Only these sources will be passed to the analyst."
                : "Add useful pages as you browse. Your selection stays with this research session."}
            </p>
            {selectedSources.length > 0 && (
              <span className="local-save-status">
                {cartSyncing ? "Saving research cart…" : "Saved to research cart"}
              </span>
            )}
            {selectedSources.length > 0 && (
              <button
                className="context-header-compile"
                type="button"
                disabled={sending}
                onClick={() => void compileAnswer()}
              >
                {sending ? "Compiling…" : "Compile answer"} <span>→</span>
              </button>
            )}
          </header>

          {selectedSources.length > 0 && (
            <section className="context-health">
              <div className="context-health-title">
                <span>Coverage</span>
                <strong>~{compactNumber(estimatedContextTokens)} tokens</strong>
              </div>
              <div className="coverage-lines">
                {contextCoverage.map((line) => (
                  <div key={line.entity}>
                    <strong>{line.entity}</strong>
                    <span>{line.periods.length ? line.periods.map((period) => period.slice(0, 4)).join(", ") : "undated"}</span>
                    <em>{line.pages}p</em>
                  </div>
                ))}
              </div>
              {selectedSources.length > 2 && (
                <div className="refine-controls">
                  <span>Refine set</span>
                  <button type="button" onClick={() => applyContextRefinement("dedupe")}>Remove adjacent</button>
                  <button type="button" onClick={() => applyContextRefinement("cap")}>Cap 3 each</button>
                  <button type="button" onClick={() => applyContextRefinement("latest")}>Latest only</button>
                </div>
              )}
            </section>
          )}

          <div className="context-sources">
            {selectedSources.length === 0 ? (
              <div className="context-empty">
                <span>＋</span>
                <strong>No evidence selected yet</strong>
                <p>Your context will stay visible here as you explore.</p>
              </div>
            ) : selectedGroups.map(([entity, sources]) => (
              <section className="context-group" key={entity}>
                <div className="context-group-title"><strong>{entity}</strong><span>{sources.length}</span></div>
                {sources.map((source) => (
                  <article className="context-source" key={sourceKey(source)}>
                    <button
                      type="button"
                      className="context-source-copy"
                      onClick={() => setReadingSource(source)}
                    >
                      <strong>{source.title}</strong>
                      <span>{source.period}{source.page_number ? ` · p.${source.page_number}` : ""}{source.industry ? ` · ${source.industry}` : ""}</span>
                      {(source.retrieval_facets || []).length > 0 && (
                        <small>{source.retrieval_facets!.join(" · ")}</small>
                      )}
                    </button>
                    <button type="button" className="remove-source" onClick={() => void toggleSource(source)} aria-label={`Remove ${source.title}`}>
                      ×
                    </button>
                    <div className="mode-toggle">
                      <button
                        type="button"
                        className={source.inclusionMode === "summary" ? "active" : ""}
                        onClick={() => updateMode(source, "summary")}
                      >
                        Summary
                      </button>
                      <button
                        type="button"
                        disabled={source.loading || !source.detail?.text}
                        className={source.inclusionMode === "full" ? "active" : ""}
                        onClick={() => updateMode(source, "full")}
                      >
                        {source.loading ? "Loading" : "Full page"}
                      </button>
                    </div>
                  </article>
                ))}
              </section>
            ))}
          </div>

          <section className="analyst-handoff">
            <label htmlFor="project-name">Project</label>
            <input
              id="project-name"
              value={sessionLabel}
              onChange={(event) => setSessionLabel(event.target.value)}
            />
            <label htmlFor="analyst-prompt">Instruction for the analyst</label>
            <textarea
              id="analyst-prompt"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={4}
            />
            <div className="context-metrics">
              <span><strong>{selectedSources.length}</strong> sources</span>
              <span><strong>{selectedGroups.length}</strong> entities</span>
              <span><strong>~{compactNumber(estimatedContextTokens)}</strong> tokens</span>
            </div>
            <button
              className="compile-button"
              type="button"
              disabled={sending || !selectedSources.length}
              onClick={() => void compileAnswer()}
            >
              {sending ? "Working with the evidence…" : "Compile answer from selected pages"}
              {!sending && <span>→</span>}
            </button>
            <div className="export-actions">
              <button type="button" disabled={!selectedSources.length} onClick={() => void copyText(JSON.stringify(apiPayload, null, 2), "JSON copied")}>
                Copy JSON
              </button>
              <button type="button" disabled={!selectedSources.length} onClick={() => void copyText(markdown, "Markdown copied")}>
                Copy bundle Markdown
              </button>
            </div>
            {compileError && <div className="error-state compact">{compileError}</div>}
          </section>

          {compiled && (
            <button
              className="output-ready"
              type="button"
              onClick={() => {
                setReadingSource(null);
                window.requestAnimationFrame(() => {
                  document.getElementById("analyst-output")?.scrollIntoView({ behavior: "smooth", block: "start" });
                });
              }}
            >
              <span><i /> Analyst output ready</span>
              <strong>Open report →</strong>
            </button>
          )}
        </aside>
      </div>

      {search && !readingSource && selectedSources.length > 0 && (
        <div className="mobile-context-bar">
          <button type="button" className="review-mobile-context" onClick={() => setContextOpen(true)}>
            <span>{selectedSources.length}</span>
            <strong>Review</strong>
          </button>
          <button type="button" className="compile-mobile-context" disabled={sending} onClick={() => void compileAnswer()}>
            <strong>{sending ? "Compiling…" : "Compile answer"}</strong>
            {!sending && <b>→</b>}
          </button>
        </div>
      )}
      {contextOpen && <button className="mobile-backdrop" type="button" onClick={() => setContextOpen(false)} aria-label="Close context" />}
      {toast && <div className="toast">{toast}</div>}
    </main>
  );
}
