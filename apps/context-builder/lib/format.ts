/**
 * Pure formatters. No runtime imports, so this module can be unit tested
 * directly and imported from both client and server code.
 */

export function compactNumber(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

/**
 * Formats a BM25 score without lying about it.
 *
 * Scores collapse toward zero for terms that appear across most of the corpus,
 * so a fixed two-decimal format renders a real 4.1e-6 as "0.00" — which reads
 * as "no match" for what is actually the top-ranked page. Rank is the useful
 * signal; this keeps the magnitude honest when the number is shown at all.
 */
export function formatScore(score: number): string {
  if (!Number.isFinite(score)) return "n/a";
  if (score === 0) return "0";
  if (score >= 0.01) return score.toFixed(2);
  return score.toExponential(1);
}
