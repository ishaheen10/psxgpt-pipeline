/**
 * Pure parameter plumbing for the corpus proxy routes.
 *
 * Kept free of runtime imports so it can be unit tested directly, and so the
 * forwarding contract below is pinned by a test rather than by review.
 */

/**
 * Parameters forwarded to `/api/search`.
 *
 * The corpus Worker parses all of these, so dropping any one of them silently
 * widens the search instead of failing. That is exactly the bug in the
 * corpus-browser proxy, which forwards only `q` and `limit`, leaving its
 * ticker, corpus, and date controls with no effect on the query.
 */
export const SEARCH_PARAMS = [
  "q",
  "limit",
  "tickers",
  "corpora",
  "date_from",
  "date_to",
] as const;

export const DOCUMENT_PARAMS = ["corpus", "id"] as const;

/** Copies the named params through, omitting ones the caller did not set. */
export function forwardParams(from: URL, to: URL, names: readonly string[]): URL {
  for (const name of names) {
    const value = from.searchParams.get(name);
    if (value !== null && value !== "") to.searchParams.set(name, value);
  }
  return to;
}
