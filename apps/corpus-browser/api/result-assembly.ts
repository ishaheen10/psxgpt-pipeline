export interface RankableResult {
  corpus: string;
  id: number;
  entity: string;
  evidence_score?: number;
  rerank_score?: number;
  fusion_score?: number;
}

function observableRankScore(result: RankableResult): number {
  return result.evidence_score
    ?? result.rerank_score
    ?? result.fusion_score
    ?? 0;
}

/**
 * Assemble a result set for a multi-company question. Relevance still decides
 * rank one. Before showing repeated pages for the same company, use the best
 * sufficiently supported page for each required company. A weak candidate is
 * left out rather than presented merely to complete a grid.
 */
export function assembleCoverageAwareResults<T extends RankableResult>(
  results: T[],
  requiredEntities: string[],
  limit: number,
): T[] {
  if (limit <= 0 || results.length === 0) return [];
  const required = [...new Set(requiredEntities.filter(Boolean))];
  if (required.length <= 1) return results.slice(0, limit);

  const selected: T[] = [];
  const seen = new Set<string>();
  const add = (result: T | undefined) => {
    if (!result || selected.length >= limit) return;
    const key = `${result.corpus}:${result.id}`;
    if (seen.has(key)) return;
    seen.add(key);
    selected.push(result);
  };

  add(results[0]);
  const strongest = Math.max(0, ...results.map(observableRankScore));
  const minimumSupport = strongest > 0 ? strongest * 0.6 : 0;
  const coverageWindow = results.slice(
    0,
    Math.min(results.length, Math.max(12, required.length * 4)),
  );
  const covered = new Set(selected.map((result) => result.entity));
  const coverageCandidates = required
    .filter((entity) => !covered.has(entity))
    .map((entity) => ({
      entity,
      result: coverageWindow.find((candidate) =>
        candidate.entity === entity
        && observableRankScore(candidate) >= minimumSupport
      ),
    }))
    .filter((candidate): candidate is { entity: string; result: T } =>
      Boolean(candidate.result)
    )
    .sort((left, right) =>
      results.indexOf(left.result) - results.indexOf(right.result)
    );
  for (const candidate of coverageCandidates) add(candidate.result);
  for (const result of results) add(result);
  return selected;
}
