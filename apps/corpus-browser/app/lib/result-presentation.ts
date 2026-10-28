import {
  resultCoverageEntities,
  resultFacetLabels,
  type AppliedSearch,
  type SearchResponse,
  type SearchResult,
} from "./retrieval-execution.ts";

export interface ResultDisplayGroup {
  key: string;
  label: string;
  sources: SearchResult[];
}

export interface ResultDisplaySection {
  key: string;
  eyebrow: "Company" | "Evidence need";
  title: string;
  sources: SearchResult[];
  groups: ResultDisplayGroup[];
}

function periodOrdinal(period: string): number {
  const dated = Date.parse(period);
  if (Number.isFinite(dated)) return dated;
  const year = Number(period.slice(0, 4));
  return Number.isInteger(year) && year >= 1900 && year <= 2100
    ? Date.UTC(year, 0, 1)
    : Number.NEGATIVE_INFINITY;
}

export function sortRecentFirst(results: SearchResult[]): SearchResult[] {
  return [...results].sort((left, right) =>
    periodOrdinal(right.period) - periodOrdinal(left.period)
    || (right.evidence_score || 0) - (left.evidence_score || 0)
    || (right.fusion_score || 0) - (left.fusion_score || 0)
    || right.bm25_score - left.bm25_score
  );
}

function groupByEntity(
  results: SearchResult[],
  preferredEntities: string[],
): Array<[string, SearchResult[]]> {
  const groups = new Map<string, SearchResult[]>();
  for (const source of results) {
    const existing = groups.get(source.entity);
    if (existing) existing.push(source);
    else groups.set(source.entity, [source]);
  }
  const order = [
    ...preferredEntities,
    ...results.map((source) => source.entity),
  ].filter((entity, index, values) => entity && values.indexOf(entity) === index);
  return order.flatMap((entity) => {
    const sources = groups.get(entity);
    return sources ? [[entity, sortRecentFirst(sources)] as [string, SearchResult[]]] : [];
  });
}

function facetGroups(
  results: SearchResult[],
  facetLabels: string[],
): ResultDisplayGroup[] {
  if (facetLabels.length === 0) {
    return [{
      key: "general",
      label: "Relevant pages",
      sources: sortRecentFirst(results),
    }];
  }
  return facetLabels.flatMap((facet) => {
    const sources = sortRecentFirst(
      results.filter((source) => (source.retrieval_facets || []).includes(facet)),
    );
    return sources.length
      ? [{ key: facet, label: facet, sources }]
      : [];
  });
}

export function buildResultDisplaySections(
  search: SearchResponse | null,
  appliedSearch: AppliedSearch | null,
): ResultDisplaySection[] {
  const results = sortRecentFirst(search?.results || []);
  if (results.length === 0) return [];
  const facets = resultFacetLabels(search, appliedSearch);
  const preferredEntities = resultCoverageEntities(search, appliedSearch);
  const multiEntity = preferredEntities.length > 1;

  if (multiEntity) {
    return groupByEntity(results, preferredEntities).map(([entity, sources]) => ({
      key: `entity:${entity}`,
      eyebrow: "Company",
      title: entity,
      sources,
      groups: facetGroups(sources, facets),
    }));
  }

  const activeFacets = facets.length ? facets : ["Relevant pages"];
  return activeFacets.flatMap((facet) => {
    const sources = facet === "Relevant pages"
      ? results
      : results.filter((source) => (source.retrieval_facets || []).includes(facet));
    if (sources.length === 0) return [];
    return [{
      key: `facet:${facet}`,
      eyebrow: "Evidence need" as const,
      title: facet,
      sources: sortRecentFirst(sources),
      groups: groupByEntity(sources, preferredEntities).map(([entity, entitySources]) => ({
        key: entity,
        label: entity,
        sources: entitySources,
      })),
    }];
  });
}
