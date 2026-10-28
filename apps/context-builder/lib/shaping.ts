/**
 * Context-set refinement.
 *
 * These mirror the shaping tools the serving engine's lane runs after
 * retrieval (`dedupe_adjacent`, `top_k_per_ticker`, `same_filing_focus`,
 * `expand_neighbors`). There they are tool calls the lane model makes; here
 * they are controls the researcher operates on their own selection.
 *
 * `expand_neighbors` is the one that changes correctness rather than tidiness.
 * The lane pack is explicit that a fixed-asset schedule "typically spans 2–4
 * consecutive pages" and that neighbour expansion is mandatory to capture the
 * whole table. Without it, selecting the one page BM25 ranked highest ships a
 * truncated schedule and nothing signals that anything is missing.
 */

import type { StackedSource } from "./corpus";

/** Identifies the document a page belongs to. */
export function filingKey(source: Pick<StackedSource, "corpus" | "entity" | "period">): string {
  return `${source.corpus}:${source.entity}:${source.period}`;
}

/**
 * Drops pages that sit directly beside a higher-ranked page from the same
 * filing. Consecutive pages usually restate the same table, so the second adds
 * tokens without adding evidence.
 */
export function dedupeAdjacent(sources: StackedSource[]): StackedSource[] {
  const ordered = [...sources].sort((a, b) => b.bm25_score - a.bm25_score);
  const kept: StackedSource[] = [];
  for (const source of ordered) {
    const tooClose = kept.some(
      (other) =>
        filingKey(other) === filingKey(source) &&
        other.page_number !== null &&
        source.page_number !== null &&
        Math.abs(other.page_number - source.page_number) <= 1,
    );
    if (!tooClose) kept.push(source);
  }
  return kept;
}

/** Caps how many pages any one company contributes, keeping the best ranked. */
export function capPerEntity(sources: StackedSource[], max: number): StackedSource[] {
  const counts = new Map<string, number>();
  return [...sources]
    .sort((a, b) => b.bm25_score - a.bm25_score)
    .filter((source) => {
      const seen = counts.get(source.entity) || 0;
      if (seen >= max) return false;
      counts.set(source.entity, seen + 1);
      return true;
    });
}

/**
 * Keeps only the most recent filing per company. Useful when a search spans
 * several years but the question is about the current position.
 */
export function latestFilingOnly(sources: StackedSource[]): StackedSource[] {
  const newest = new Map<string, string>();
  for (const source of sources) {
    const current = newest.get(source.entity);
    if (!current || source.period > current) newest.set(source.entity, source.period);
  }
  return sources.filter((source) => newest.get(source.entity) === source.period);
}

/**
 * Page ids to try when expanding around a page.
 *
 * Ids run consecutively within a filing in `financial_documents_new`, verified
 * against the production database. Each candidate is still validated against
 * the fetched row's ticker, filing type, and period before being accepted, so a
 * page that runs off either end of the document is rejected rather than
 * silently pulling in a neighbouring filing.
 */
export function neighborIds(id: number, radius: number): number[] {
  const ids: number[] = [];
  for (let offset = -radius; offset <= radius; offset += 1) {
    if (offset !== 0 && id + offset > 0) ids.push(id + offset);
  }
  return ids;
}

/** True when a fetched neighbour really belongs to the same document. */
export function sameDocument(
  origin: { entity: string; period: string },
  candidate: { ticker?: unknown; filing_period?: unknown; filing_type?: unknown },
  originFilingType?: unknown,
): boolean {
  const ticker = String(candidate.ticker || "").toUpperCase();
  const period = String(candidate.filing_period || "");
  if (!ticker || !period) return false;
  if (ticker !== origin.entity.toUpperCase()) return false;
  if (period !== origin.period) return false;
  if (originFilingType && String(candidate.filing_type || "") !== String(originFilingType)) {
    return false;
  }
  return true;
}

export interface CoverageLine {
  entity: string;
  periods: string[];
  pages: number;
}

/**
 * Per-company coverage of the assembled set.
 *
 * The serving engine treats coverage as a first-class lane output and requires
 * the analyst to disclose insufficiency rather than answer quietly from thin
 * evidence. The same fact belongs in front of the human while they are still
 * choosing pages.
 */
export function coverage(sources: StackedSource[]): CoverageLine[] {
  const byEntity = new Map<string, { periods: Set<string>; pages: number }>();
  for (const source of sources) {
    const entry = byEntity.get(source.entity) || { periods: new Set<string>(), pages: 0 };
    if (source.period) entry.periods.add(source.period);
    entry.pages += 1;
    byEntity.set(source.entity, entry);
  }
  return [...byEntity.entries()]
    .map(([entity, entry]) => ({
      entity,
      periods: [...entry.periods].sort().reverse(),
      pages: entry.pages,
    }))
    .sort((a, b) => a.entity.localeCompare(b.entity));
}
