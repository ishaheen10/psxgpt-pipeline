import type { CorpusKind, DocumentDetail } from "./corpus";

/**
 * Caches fetched pages for the lifetime of the session.
 *
 * Flipping through a filing revisits the same pages constantly — back one,
 * forward two, back again — and each page is a full document fetch plus a live
 * R2 head check. Without a cache that browsing feels laggy, which is precisely
 * the interaction that has to feel immediate.
 */

const cache = new Map<string, DocumentDetail>();
const inFlight = new Map<string, Promise<DocumentDetail | null>>();

function key(corpus: CorpusKind, id: number): string {
  return `${corpus}:${id}`;
}

export function cachedDocument(corpus: CorpusKind, id: number): DocumentDetail | undefined {
  return cache.get(key(corpus, id));
}

export async function loadDocument(
  corpus: CorpusKind,
  id: number,
): Promise<DocumentDetail | null> {
  const cacheKey = key(corpus, id);
  const cached = cache.get(cacheKey);
  if (cached) return cached;

  // Two neighbours resolving to the same page must not fire two requests.
  const pending = inFlight.get(cacheKey);
  if (pending) return pending;

  const request = (async () => {
    try {
      const response = await fetch(`/api/document?corpus=${corpus}&id=${id}`);
      if (!response.ok) return null;
      const detail = (await response.json()) as DocumentDetail;
      cache.set(cacheKey, detail);
      return detail;
    } catch {
      return null;
    } finally {
      inFlight.delete(cacheKey);
    }
  })();

  inFlight.set(cacheKey, request);
  return request;
}
