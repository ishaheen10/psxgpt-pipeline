"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DocumentDetail, SearchResult } from "@/lib/corpus";
import { cachedDocument, loadDocument } from "@/lib/document-cache";
import { MarkdownPage } from "@/lib/markdown";
import { sameDocument } from "@/lib/shaping";

/**
 * Reads a filing as a document.
 *
 * The previous version paged through a five-chip window, which meant clicking
 * back and forth to answer a question a reader answers by scrolling: does this
 * table continue? So this scrolls. Pages append as you reach the bottom, and
 * each page carries its own clip button, so choosing a page is a decision made
 * while reading it rather than afterwards from a list.
 *
 * Page ids run consecutively within a filing, so the next page is the next id.
 * Each one is still checked against the anchor's ticker, filing type, and
 * period, so the document simply ends rather than running into the next filing.
 */

/** Pages either side of the anchor loaded before the reader first paints. */
const INITIAL_BEFORE = 1;
const INITIAL_AFTER = 2;
const STEP = 2;
/**
 * Ceiling on pages auto-appended in one sitting. Scroll-driven loading can
 * otherwise keep firing while the reader sits at the end of a long filing,
 * fetching far more of the document than anyone asked for.
 */
const MAX_AUTO_PAGES = 24;

interface Loaded {
  id: number;
  page: number;
  detail: DocumentDetail | null;
  valid: boolean;
}

interface Props {
  origin: SearchResult;
  isInContext: (id: number) => boolean;
  onToggle: (id: number, detail: DocumentDetail, pageNumber: number | null) => void;
  onClose: () => void;
}

export default function DocumentReader({ origin, isInContext, onToggle, onClose }: Props) {
  const [firstId, setFirstId] = useState(Math.max(1, origin.id - INITIAL_BEFORE));
  const [lastId, setLastId] = useState(origin.id + INITIAL_AFTER);
  const [pages, setPages] = useState<Record<number, Loaded>>({});
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const anchorRef = useRef<HTMLElement | null>(null);

  const originFilingType = cachedDocument(origin.corpus, origin.id)?.metadata?.filing_type;

  const pageNumberFor = useCallback(
    (id: number) => (origin.page_number ?? 0) + (id - origin.id),
    [origin.page_number, origin.id],
  );

  const ingest = useCallback(
    (id: number, detail: DocumentDetail | null) => {
      const valid =
        Boolean(detail) &&
        (id === origin.id || sameDocument(origin, detail?.metadata || {}, originFilingType));
      setPages((current) => ({
        ...current,
        [id]: { id, page: pageNumberFor(id), detail, valid },
      }));
      return valid;
    },
    [origin, originFilingType, pageNumberFor],
  );

  // Anything in range that has not been fetched yet. Deriving this rather than
  // holding a `busy` flag keeps the effect free of synchronous state updates.
  const pending = useMemo(() => {
    const wanted: number[] = [];
    for (let id = firstId; id <= lastId; id += 1) {
      if (id > 0 && !pages[id]) wanted.push(id);
    }
    return wanted;
  }, [firstId, lastId, pages]);
  const busy = pending.length > 0;

  useEffect(() => {
    if (pending.length === 0) return;
    let cancelled = false;
    for (const id of pending) {
      loadDocument(origin.corpus, id).then((detail) => {
        if (!cancelled) ingest(id, detail);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [pending, origin.corpus, ingest]);

  // Bring the page the researcher actually searched for into view, so opening
  // a document does not start them at whatever happened to load first.
  useEffect(() => {
    const node = anchorRef.current;
    if (node && pages[origin.id]) {
      node.scrollIntoView({ block: "start" });
    }
    // Only on first successful load of the anchor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(pages[origin.id])]);

  const atCap = lastId - firstId + 1 >= MAX_AUTO_PAGES;
  const reachedEnd = Boolean(pages[lastId]) && !pages[lastId].valid;

  // Append as the reader reaches the end, so scrolling just continues reading.
  useEffect(() => {
    const sentinel = bottomRef.current;
    // Stop at the end of the filing rather than fetching into the next one,
    // and stop once the session cap is reached.
    if (!sentinel || busy || reachedEnd || atCap) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) setLastId((id) => id + STEP);
      },
      { root: sentinel.closest(".doc-scroll"), rootMargin: "150px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [busy, reachedEnd, atCap]);

  const visible = useMemo(() => {
    const list: Loaded[] = [];
    for (let id = firstId; id <= lastId; id += 1) {
      const entry = pages[id];
      if (entry?.valid) list.push(entry);
    }
    return list;
  }, [firstId, lastId, pages]);

  const clippedCount = visible.filter((entry) => isInContext(entry.id)).length;
  const earlierAvailable = firstId > 1 && pages[firstId]?.valid !== false;

  return (
    <section className="doc" aria-label={`${origin.entity} filing`}>
      <header className="doc-head">
        <button type="button" className="doc-back" onClick={onClose}>
          ← results
        </button>
        <div className="doc-id">
          <strong>{origin.entity}</strong>
          <span>
            {origin.title.replace(/ — page \d+$/, "")} · {origin.period}
          </span>
        </div>
        <div className="doc-count">
          {clippedCount > 0 ? `${clippedCount} clipped` : "reading"}
        </div>
      </header>

      <div className="doc-scroll">
        {earlierAvailable && (
          <button
            type="button"
            className="doc-more"
            onClick={() => setFirstId((id) => Math.max(1, id - STEP))}
          >
            ↑ earlier pages
          </button>
        )}

        {visible.length === 0 && <div className="doc-loading">Loading document…</div>}

        {visible.map((entry) => {
          const clipped = isInContext(entry.id);
          const isAnchor = entry.id === origin.id;
          return (
            <article
              key={entry.id}
              ref={isAnchor ? anchorRef : undefined}
              className={`doc-page ${clipped ? "clipped" : ""} ${isAnchor ? "anchor" : ""}`}
            >
              <div className="doc-page-bar">
                <span className="doc-page-no">
                  Page {entry.page}
                  {isAnchor && <em>search hit</em>}
                </span>
                <button
                  type="button"
                  className={`clip-btn ${clipped ? "on" : ""}`}
                  onClick={() => entry.detail && onToggle(entry.id, entry.detail, entry.page)}
                >
                  {clipped ? "✓ clipped" : "+ clip page"}
                </button>
              </div>
              <MarkdownPage text={entry.detail?.text || ""} />
            </article>
          );
        })}

        <div ref={bottomRef} className="doc-sentinel">
          {reachedEnd ? (
            "End of filing"
          ) : busy ? (
            "Loading…"
          ) : atCap ? (
            <button type="button" className="doc-more" onClick={() => setLastId((id) => id + STEP)}>
              ↓ keep reading
            </button>
          ) : (
            ""
          )}
        </div>
      </div>
    </section>
  );
}
