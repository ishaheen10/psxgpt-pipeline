import type { SearchResult } from "./corpus";

/**
 * Collapses the same disclosure repeated across filing years.
 *
 * Annual reports restate boilerplate almost verbatim, so BM25 — which has no
 * notion of a document family — happily fills a result set with one page from
 * four consecutive years. Observed on a real query: four of six MCB slots were
 * the same share-price-sensitivity table from 2021 through 2024, pushing the
 * page that actually discussed deposit cost down to rank five.
 *
 * Rather than drop the repeats, they collapse into one row with the other years
 * offered alongside. Which year a researcher wants is their decision, not one a
 * ranking function should make silently.
 */

const STOP = new Set([
  "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "at", "by",
  "is", "are", "was", "were", "as", "with", "from", "that", "this",
]);

/** Content tokens of a page summary, used to judge whether two pages restate each other. */
function tokens(text: string): Set<string> {
  return new Set(
    (text || "")
      .toLowerCase()
      .match(/[a-z]{3,}/g)
      ?.filter((token) => !STOP.has(token)) || [],
  );
}

/**
 * Overlap of two summaries, as shared tokens over the smaller token set.
 *
 * Jaccard is the wrong measure here. A restatement routinely swaps details —
 * MCB's 2024 risk page replaces "political stability law order" with a market
 * capitalisation — and Jaccard charges for every such difference twice, scoring
 * an obvious restatement at 0.62 while an unrelated page scores 0.07. Overlap
 * asks the question that actually matters: is one page essentially contained in
 * the other? The same pair scores 0.81 against 0.07.
 *
 * Numbers are excluded from tokens entirely, since the figures are exactly what
 * legitimately differ between years.
 */
export function summarySimilarity(a: string, b: string): number {
  const left = tokens(a);
  const right = tokens(b);
  if (left.size === 0 || right.size === 0) return 0;
  let shared = 0;
  for (const token of left) if (right.has(token)) shared += 1;
  return shared / Math.min(left.size, right.size);
}

/** High enough that only genuine restatements collapse, not merely related pages. */
export const RECURRENCE_THRESHOLD = 0.72;

export interface ResultFamily {
  /** Best-scoring member, shown by default. */
  primary: SearchResult;
  /** The same disclosure in other filings, newest first. Empty when unique. */
  variants: SearchResult[];
}

export function collapseRecurring(
  results: SearchResult[],
  threshold = RECURRENCE_THRESHOLD,
): ResultFamily[] {
  const families: ResultFamily[] = [];

  for (const result of results) {
    const match = families.find((family) => {
      // Only ever collapse within one company: two banks restating the same
      // regulatory boilerplate are two pieces of evidence, not one.
      if (family.primary.entity !== result.entity) return false;
      // A page cannot be a different year of itself.
      if (family.primary.period === result.period) return false;
      if (family.variants.some((variant) => variant.period === result.period)) return false;
      return summarySimilarity(family.primary.summary, result.summary) >= threshold;
    });

    if (match) match.variants.push(result);
    else families.push({ primary: result, variants: [] });
  }

  for (const family of families) {
    // Newest first: the current year is the usual default choice.
    family.variants.sort((a, b) => b.period.localeCompare(a.period));
  }
  return families;
}

/** All members of a family, newest period first, for the year picker. */
export function familyMembers(family: ResultFamily): SearchResult[] {
  return [family.primary, ...family.variants].sort((a, b) => b.period.localeCompare(a.period));
}
