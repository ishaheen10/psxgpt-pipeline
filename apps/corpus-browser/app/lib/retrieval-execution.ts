export type CorpusKind = "filing" | "announcement" | "macro";

export interface QualityFlag {
  flag_id: string;
  severity: string;
  status: string;
  category?: string | null;
  metric?: string | null;
  rule?: string | null;
  message?: string | null;
}

export interface SearchResult {
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
  availability?: {
    indexed: boolean;
    parsed: boolean;
    r2_linked: boolean;
  };
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
  retrieval_lanes?: Array<
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
    | "indexed_heading"
  >;
  fusion_score?: number;
  rerank_score?: number;
  evidence_score?: number;
  corpus_support?: number;
  derived_heading?: string;
  table_signature?: string[];
  page_signals?: {
    title: string;
    headings: string[];
    tableSignature: string[];
    tableRows: number;
    numericDensity: number;
    navigationLikelihood: number;
  };
  page_index?: {
    schema_version: string;
    display_summary: string;
    parse_status: "ok" | "incomplete" | "reextract";
    metadata_alignment: "aligned" | "unclear" | "conflict";
  };
  retrieval_facets?: string[];
}

export interface CatalogFacet {
  key: "entity" | "industry" | "period" | "corpus" | "publisher";
  label: string;
  selection_mode: "or";
  values: Array<{
    value: string;
    label: string;
    count: number;
    selected: boolean;
  }>;
}

export interface FieldQueries {
  key: string;
  summary: string;
  text: string;
  heading: string;
}

export interface SearchFacet {
  label: string;
  evidenceNeed?: string;
  lexicalQuery: string;
  fieldQueries: FieldQueries;
  corpora?: CorpusKind[];
  filingCadence?: "annual" | "quarterly" | "any";
  filingCadenceBasis?: "explicit" | "evidence_structure" | "not_applicable";
  temporalMode?: "bounded" | "latest" | "recent" | "all";
}

export interface SearchResponse {
  catalog_schema_version?: string;
  query: string;
  parsed_terms: string[];
  match_mode: "all" | "any" | "all+any";
  result_count: number;
  results: SearchResult[];
  retrieval_version?: string;
  retrieval_strategy?: "statement_pack" | "field_rrf_v1" | "evidence_scout_rrf_v2";
  field_queries?: FieldQueries;
  broadening_applied?: boolean;
  latest_periods?: Record<string, string>;
  result_facets?: CatalogFacet[];
  search_facets?: SearchFacet[];
  coverage_gaps?: Array<{
    ticker: string;
    year: number;
    missing_sections: string[];
  }>;
  diagnostics?: unknown;
}

export interface AppliedSearch {
  naturalQuery: string;
  literalQuery: string;
  lexicalQuery: string;
  fieldQueries: FieldQueries;
  evidenceNeeds?: string[];
  searchFacets?: SearchFacet[];
  tickers: string[];
  requiredTickers?: string[];
  supportingTickers?: string[];
  industries: string[];
  entities?: Array<{
    ticker: string;
    company_name: string;
    mention: string;
    confidence: number;
    role?: "required" | "supporting";
  }>;
  unresolvedEntities?: string[];
  years: number[];
  dateFrom: string | null;
  dateTo: string | null;
  corpora: CorpusKind[];
  filingCadence?: "annual" | "quarterly" | "any";
  filingCadenceBasis?: "explicit" | "evidence_structure" | "not_applicable";
  temporalMode?: "bounded" | "latest" | "recent" | "all";
  latest?: boolean;
  preferRecent?: boolean;
  workflow?: "browse" | "financial_model";
  queryPack?: "financial_statements" | null;
  interpretation?: string;
  routedBy?: "router" | "manual";
}

export type RetrievalFetcher = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export interface RetrievalExecutionOptions {
  fetcher: RetrievalFetcher;
  searchPath?: string;
  debug?: boolean;
  limit?: number;
  retrievalMode?: "baseline" | "structure" | "key_only" | "combined";
}

function sourceKey(source: Pick<SearchResult, "corpus" | "id">): string {
  return `${source.corpus}:${source.id}`;
}

export function resultFacetLabels(
  search: SearchResponse | null,
  interpreted: AppliedSearch | null,
): string[] {
  const routed = interpreted?.searchFacets?.map((facet) => facet.label) || [];
  const returned = (search?.results || []).flatMap((result) => result.retrieval_facets || []);
  return [...new Set([...routed, ...returned])];
}

export function resultCoverageEntities(
  search: SearchResponse | null,
  interpreted: AppliedSearch | null,
): string[] {
  const returned = (search?.results || []).map((source) => source.entity).filter(Boolean);
  return [...new Set([
    ...(interpreted?.requiredTickers || interpreted?.tickers || []),
    ...(interpreted?.supportingTickers || []),
    ...returned,
  ])];
}

export function selectEvidencePreview(
  search: SearchResponse | null,
  interpreted: AppliedSearch | null,
  maxSources = 12,
): Array<{ facet: string; source: SearchResult }> {
  const facets = resultFacetLabels(search, interpreted);
  const entities = resultCoverageEntities(search, interpreted);
  const picks: Array<{ facet: string; source: SearchResult }> = [];
  const seen = new Set<string>();
  for (const facet of facets.length ? facets : ["General evidence"]) {
    for (const entity of entities) {
      const source = (search?.results || []).find((candidate) =>
        candidate.entity === entity
        && (
          facet === "General evidence"
          || (candidate.retrieval_facets || []).includes(facet)
        )
      );
      if (!source || seen.has(sourceKey(source))) continue;
      seen.add(sourceKey(source));
      picks.push({ facet, source });
    }
  }
  return picks.slice(0, maxSources);
}

export function selectContextBundle(
  search: SearchResponse | null,
  interpreted: AppliedSearch | null,
  maxSources = 16,
  excludedKeys: ReadonlySet<string> = new Set(),
): SearchResult[] {
  const selected: SearchResult[] = [];
  const seen = new Set(excludedKeys);
  const add = (source: SearchResult | undefined) => {
    if (!source || selected.length >= maxSources || seen.has(sourceKey(source))) return;
    seen.add(sourceKey(source));
    selected.push(source);
  };

  for (const pick of selectEvidencePreview(search, interpreted, maxSources)) add(pick.source);

  if ((interpreted?.years.length || 0) > 1) {
    const periods = new Set<string>();
    for (const source of [...(search?.results || [])].sort(
      (left, right) => right.bm25_score - left.bm25_score,
    )) {
      const periodKey = `${source.entity}:${source.period}`;
      if (periods.has(periodKey)) continue;
      periods.add(periodKey);
      add(source);
    }
  }

  const minimumBundle = Math.min(3, maxSources);
  for (const source of search?.results || []) {
    if (selected.length >= minimumBundle) break;
    add(source);
  }
  return selected;
}

export function deriveResultFacets(results: SearchResult[]): CatalogFacet[] {
  const labels: Record<CorpusKind, string> = {
    filing: "Company filings",
    announcement: "PSX announcements",
    macro: "Economic & policy sources",
  };
  const definitions: Array<{
    key: CatalogFacet["key"];
    label: string;
    value: (result: SearchResult) => { value: string; label: string } | null;
    max: number;
  }> = [
    { key: "entity", label: "Company or source", value: (result) => result.entity ? { value: result.entity, label: result.entity } : null, max: 12 },
    {
      key: "industry",
      label: "Industry",
      value: (result) => result.industry_id && result.industry
        ? { value: result.industry_id, label: result.industry }
        : null,
      max: 8,
    },
    {
      key: "period",
      label: "Period",
      value: (result) => /^\d{4}$/.test(result.period.slice(0, 4))
        ? { value: result.period.slice(0, 4), label: result.period.slice(0, 4) }
        : null,
      max: 10,
    },
    {
      key: "corpus",
      label: "Source type",
      value: (result) => ({ value: result.corpus, label: labels[result.corpus] }),
      max: 3,
    },
  ];
  return definitions.flatMap((definition) => {
    const counts = new Map<string, { label: string; count: number }>();
    for (const result of results) {
      const item = definition.value(result);
      if (!item) continue;
      const current = counts.get(item.value);
      counts.set(item.value, { label: item.label, count: (current?.count || 0) + 1 });
    }
    if (counts.size <= 1) return [];
    return [{
      key: definition.key,
      label: definition.label,
      selection_mode: "or" as const,
      values: [...counts.entries()]
        .map(([value, item]) => ({ value, label: item.label, count: item.count, selected: false }))
        .sort((left, right) => right.count - left.count || left.label.localeCompare(right.label))
        .slice(0, definition.max),
    }];
  });
}

export function mergeFacetResponses(
  facets: SearchFacet[],
  responses: SearchResponse[],
  requiredEntities: string[],
  supportingEntities: string[] = [],
): SearchResponse {
  const facetResults = responses.map((response, index) =>
    response.results.map((result) => ({
      ...result,
      retrieval_facets: [facets[index].label],
    })),
  );
  const seen = new Map<string, SearchResult & { bestFacetRank: number; discoveryOrder: number }>();
  let discoveryOrder = 0;
  for (const results of facetResults) {
    results.forEach((result, rank) => {
      const key = sourceKey(result);
      const existing = seen.get(key);
      if (existing) {
        existing.retrieval_facets = [...new Set([
          ...(existing.retrieval_facets || []),
          ...(result.retrieval_facets || []),
        ])];
        existing.bestFacetRank = Math.min(existing.bestFacetRank, rank);
        return;
      }
      seen.set(key, {
        ...result,
        bestFacetRank: rank,
        discoveryOrder: discoveryOrder++,
      });
    });
  }

  const ranked = [...seen.values()].sort((left, right) =>
    left.bestFacetRank - right.bestFacetRank
    || right.retrieval_facets!.length - left.retrieval_facets!.length
    || left.discoveryOrder - right.discoveryOrder
  );
  const merged: SearchResult[] = [];
  const selected = new Set<string>();
  const add = (result: SearchResult | undefined) => {
    if (!result) return;
    const key = sourceKey(result);
    if (selected.has(key)) return;
    selected.add(key);
    merged.push(seen.get(key) || result);
  };

  // Build the useful evidence grid first: one strong page for every requested
  // evidence need and required entity when retrieval actually found one.
  for (const facet of facets) {
    const facetIndex = facets.indexOf(facet);
    const responseResults = facetResults[facetIndex] || [];
    for (const entity of requiredEntities) {
      add(responseResults.find((result) => result.entity === entity));
    }
    if (requiredEntities.length === 0) add(responseResults[0]);
  }
  // Related entities are corroborating evidence, not substitutes for required
  // company coverage.
  for (const entity of supportingEntities) {
    add(ranked.find((result) => result.entity === entity));
  }
  for (const result of ranked) add(result);

  return {
    query: responses[0]?.query || "",
    parsed_terms: [...new Set(responses.flatMap((response) => response.parsed_terms || []))],
    match_mode: responses.some((response) => response.match_mode === "all+any") ? "all+any" : "any",
    result_count: merged.length,
    results: merged,
    retrieval_version: responses[0]?.retrieval_version,
    retrieval_strategy: responses[0]?.retrieval_strategy || "field_rrf_v1",
    broadening_applied: responses.some((response) => response.broadening_applied),
    catalog_schema_version: responses[0]?.catalog_schema_version || "ledger-catalog.v1",
    result_facets: deriveResultFacets(merged),
    search_facets: facets,
    coverage_gaps: responses.flatMap((response) => response.coverage_gaps || []),
    diagnostics: responses.map((response) => response.diagnostics).filter(Boolean),
  };
}

export function buildSearchParams(
  interpreted: AppliedSearch,
  lexicalQuery: string,
  fieldQueries: FieldQueries,
  limit: number,
  scopedCorpora: CorpusKind[],
  debug = false,
  retrievalMode?: RetrievalExecutionOptions["retrievalMode"],
  scope?: Pick<SearchFacet, "evidenceNeed" | "filingCadence" | "temporalMode">,
): URLSearchParams {
  const params = new URLSearchParams({
    q: lexicalQuery,
    q_literal: scope?.evidenceNeed || interpreted.literalQuery,
    limit: String(limit),
    corpora: scopedCorpora.join(","),
    q_key: fieldQueries.key,
    q_summary: fieldQueries.summary,
    q_text: fieldQueries.text,
    q_heading: fieldQueries.heading,
  });
  const requiredTickers = interpreted.requiredTickers?.length
    ? interpreted.requiredTickers
    : interpreted.tickers;
  if (requiredTickers.length) params.set("tickers", requiredTickers.join(","));
  if (requiredTickers.length) params.set("required_tickers", requiredTickers.join(","));
  if (interpreted.industries.length) params.set("industries", interpreted.industries.join(","));
  if (interpreted.tickers.length && interpreted.years.length) params.set("years", interpreted.years.join(","));
  if (interpreted.dateFrom) params.set("date_from", interpreted.dateFrom);
  if (interpreted.dateTo) params.set("date_to", interpreted.dateTo);
  const filingCadence = scope?.filingCadence || interpreted.filingCadence;
  if (filingCadence && filingCadence !== "any") {
    params.set("filing_cadence", filingCadence);
  }
  const temporalMode = scope?.temporalMode || interpreted.temporalMode;
  const latest = temporalMode ? temporalMode === "latest" : interpreted.latest;
  if (latest) params.set("latest", "1");
  const preferRecent = !latest && (
    temporalMode
      ? temporalMode !== "latest"
      : interpreted.preferRecent !== false
  );
  if (preferRecent) params.set("prefer_recent", "1");
  if (debug) params.set("debug", "1");
  if (retrievalMode) params.set("retrieval_mode", retrievalMode);
  return params;
}

export async function executeRetrievalPlan(
  interpreted: AppliedSearch,
  options: RetrievalExecutionOptions,
): Promise<SearchResponse> {
  const searchPath = options.searchPath || "/api/search";
  const retrieve = async (
    lexicalQuery: string,
    fieldQueries: FieldQueries,
    limit: number,
    scopedCorpora: CorpusKind[],
    scope?: Pick<SearchFacet, "evidenceNeed" | "filingCadence" | "temporalMode">,
  ): Promise<SearchResponse> => {
    const params = buildSearchParams(
      interpreted,
      lexicalQuery,
      fieldQueries,
      limit,
      scopedCorpora,
      options.debug,
      options.retrievalMode,
      scope,
    );
    const response = await options.fetcher(`${searchPath}?${params.toString()}`);
    const data = await response.json() as SearchResponse & { error?: string };
    if (!response.ok) throw new Error(data.error || "Search failed");
    return data;
  };

  const facets = interpreted.queryPack ? [] : (interpreted.searchFacets || []);
  if (facets.length === 0) {
    return retrieve(
      interpreted.lexicalQuery,
      interpreted.fieldQueries,
      options.limit || 6,
      interpreted.corpora,
      undefined,
    );
  }

  const facetLimit = facets.length >= 3 ? 1 : facets.length === 2 ? 4 : 6;
  const responses = await Promise.all(
    facets.map((facet) => retrieve(
      facet.lexicalQuery,
      facet.fieldQueries,
      facetLimit,
      interpreted.routedBy === "manual"
        ? interpreted.corpora
        : facet.corpora?.length
          ? facet.corpora
          : interpreted.corpora,
      facet,
    )),
  );
  return mergeFacetResponses(
    facets,
    responses,
    interpreted.requiredTickers || interpreted.tickers,
    interpreted.supportingTickers || [],
  );
}
