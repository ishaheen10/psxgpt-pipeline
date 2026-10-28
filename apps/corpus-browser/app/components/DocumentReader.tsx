"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { authenticatedFetch } from "../lib/auth-client";

type CorpusKind = "filing" | "announcement" | "macro";

interface ReaderOrigin {
  id: number;
  corpus: CorpusKind;
  entity: string;
  title: string;
  period: string;
  page_number: number | null;
}

export interface ReaderDocumentDetail {
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
  quality_flags: Array<{
    flag_id: string;
    severity: string;
    status: string;
    category?: string | null;
    metric?: string | null;
    rule?: string | null;
    message?: string | null;
  }>;
}

interface LoadedPage {
  id: number;
  pageNumber: number | null;
  detail: ReaderDocumentDetail;
}

interface Props {
  origin: ReaderOrigin;
  isSelected: (corpus: CorpusKind, id: number) => boolean;
  onToggle: (
    origin: ReaderOrigin,
    id: number,
    detail: ReaderDocumentDetail,
    pageNumber: number | null,
  ) => void;
  onClose: () => void;
}

const detailCache = new Map<string, ReaderDocumentDetail>();
const LOAD_STEP = 2;
const MAX_PAGES = 24;

function cacheKey(corpus: CorpusKind, id: number) {
  return `${corpus}:${id}`;
}

async function loadDetail(corpus: CorpusKind, id: number): Promise<ReaderDocumentDetail | null> {
  const key = cacheKey(corpus, id);
  const cached = detailCache.get(key);
  if (cached) return cached;
  try {
    const response = await authenticatedFetch(`/api/document?corpus=${corpus}&id=${id}`);
    if (!response.ok) return null;
    const detail = await response.json() as ReaderDocumentDetail;
    detailCache.set(key, detail);
    return detail;
  } catch {
    return null;
  }
}

function belongsToFiling(
  origin: ReaderOrigin,
  anchor: ReaderDocumentDetail,
  candidate: ReaderDocumentDetail,
) {
  const anchorMeta = anchor.metadata || {};
  const candidateMeta = candidate.metadata || {};
  const anchorTicker = String(anchorMeta.ticker || origin.entity).toUpperCase();
  const candidateTicker = String(candidateMeta.ticker || "").toUpperCase();
  const anchorPeriod = String(anchorMeta.filing_period || origin.period);
  const candidatePeriod = String(candidateMeta.filing_period || "");
  const anchorType = String(anchorMeta.filing_type || "");
  const candidateType = String(candidateMeta.filing_type || "");
  return Boolean(
    candidateTicker
      && candidatePeriod
      && candidateTicker === anchorTicker
      && candidatePeriod === anchorPeriod
      && (!anchorType || candidateType === anchorType),
  );
}

export default function DocumentReader({
  origin,
  isSelected,
  onToggle,
  onClose,
}: Props) {
  const [pages, setPages] = useState<Record<number, LoadedPage>>({});
  const [anchor, setAnchor] = useState<ReaderDocumentDetail | null>(null);
  const [before, setBefore] = useState(origin.corpus === "filing" ? 1 : 0);
  const [after, setAfter] = useState(origin.corpus === "filing" ? 2 : 0);
  const [anchorStatus, setAnchorStatus] = useState<"loading" | "ready" | "error">("loading");
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [reachedStart, setReachedStart] = useState(false);
  const [reachedEnd, setReachedEnd] = useState(false);
  const anchorRef = useRef<HTMLElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadDetail(origin.corpus, origin.id).then((detail) => {
      if (cancelled) return;
      if (!detail) {
        setAnchorStatus("error");
        return;
      }
      setAnchor(detail);
      setPages({
        [origin.id]: {
          id: origin.id,
          pageNumber: origin.page_number,
          detail,
        },
      });
      setAnchorStatus("ready");
    });
    return () => {
      cancelled = true;
    };
  }, [origin]);

  const pendingIds = useMemo(() => {
    if (!anchor || origin.corpus !== "filing") return [];
    const first = Math.max(1, origin.id - before);
    const last = origin.id + after;
    const ids: number[] = [];
    for (let id = first; id <= last; id += 1) {
      if (id !== origin.id && !pages[id]) ids.push(id);
    }
    return ids;
  }, [after, anchor, before, origin.corpus, origin.id, pages]);
  const loading = anchorStatus === "loading" || pendingIds.length > 0;

  useEffect(() => {
    if (!anchor || !pendingIds.length) return;
    let cancelled = false;
    Promise.all(pendingIds.map(async (id) => ({ id, detail: await loadDetail(origin.corpus, id) })))
      .then((loaded) => {
        if (cancelled) return;
        const accepted: Record<number, LoadedPage> = {};
        for (const entry of loaded) {
          if (entry.detail && belongsToFiling(origin, anchor, entry.detail)) {
            accepted[entry.id] = {
              id: entry.id,
              pageNumber: (origin.page_number ?? 0) + entry.id - origin.id,
              detail: entry.detail,
            };
          } else if (entry.id < origin.id) {
            setReachedStart(true);
          } else {
            setReachedEnd(true);
          }
        }
        setPages((current) => ({ ...current, ...accepted }));
      });
    return () => {
      cancelled = true;
    };
  }, [anchor, origin, pendingIds]);

  const visiblePages = useMemo(
    () => Object.values(pages).sort((left, right) => left.id - right.id),
    [pages],
  );
  const atCap = before + after + 1 >= MAX_PAGES;

  useEffect(() => {
    if (!pages[origin.id]) return;
    window.requestAnimationFrame(() => anchorRef.current?.scrollIntoView({ block: "start" }));
  }, [origin.id, pages]);

  useEffect(() => {
    const sentinel = bottomRef.current;
    if (!sentinel || loading || reachedEnd || atCap || origin.corpus !== "filing") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) setAfter((value) => value + LOAD_STEP);
      },
      { root: sentinel.closest(".source-reader-scroll"), rootMargin: "180px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [atCap, loading, origin.corpus, reachedEnd]);

  const clipped = visiblePages.filter((page) => isSelected(origin.corpus, page.id)).length;

  async function copyPageMarkdown(page: LoadedPage) {
    await navigator.clipboard.writeText(page.detail.text);
    setCopiedId(page.id);
    window.setTimeout(() => setCopiedId((current) => current === page.id ? null : current), 1600);
  }

  return (
    <section className="source-reader" aria-label={`${origin.entity} source reader`}>
      <header className="source-reader-header">
        <button type="button" className="source-reader-back" onClick={onClose}>← Results</button>
        <div>
          <span className="section-label">Source reader</span>
          <h2>{origin.entity} · {origin.title.replace(/ — page \d+$/i, "")}</h2>
          <p>{origin.period || "Undated"} · Read around the match and select only the pages the analyst needs.</p>
        </div>
        <span className="source-reader-count">{clipped ? `${clipped} selected` : "Reading"}</span>
      </header>

      <div className="source-reader-scroll">
        {origin.corpus === "filing" && !reachedStart && (
          <button
            type="button"
            className="source-reader-more"
            disabled={loading}
            onClick={() => setBefore((value) => value + LOAD_STEP)}
          >
            ↑ Load earlier pages
          </button>
        )}

        {anchorStatus === "error" && (
          <div className="source-reader-empty">This source page could not be loaded.</div>
        )}

        {visiblePages.map((page) => {
          const selected = isSelected(origin.corpus, page.id);
          const isAnchor = page.id === origin.id;
          return (
            <article
              key={page.id}
              ref={isAnchor ? anchorRef : undefined}
              className={`source-page ${selected ? "clipped" : ""} ${isAnchor ? "search-hit" : ""}`}
            >
              <div className="source-page-toolbar">
                <span>
                  {page.pageNumber ? `Page ${page.pageNumber}` : origin.title}
                  {isAnchor && <em>Search hit</em>}
                </span>
                <div>
                  {page.detail.source_url && (
                    <a href={page.detail.source_url} target="_blank" rel="noreferrer">
                      {origin.corpus === "announcement" ? "View announcement ↗" : "View PDF page ↗"}
                    </a>
                  )}
                  <button
                    type="button"
                    className="copy-page-markdown"
                    onClick={() => void copyPageMarkdown(page)}
                  >
                    {copiedId === page.id ? "Copied" : "Copy Markdown"}
                  </button>
                  <button
                    type="button"
                    className={selected ? "selected" : ""}
                    onClick={() => onToggle(origin, page.id, page.detail, page.pageNumber)}
                  >
                    {selected ? "✓ In context" : "+ Add page to context"}
                  </button>
                </div>
              </div>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{page.detail.text}</ReactMarkdown>
            </article>
          );
        })}

        <div ref={bottomRef} className="source-reader-sentinel">
          {loading
            ? "Loading pages…"
            : reachedEnd
              ? "End of filing"
              : atCap
                ? (
                  <button type="button" className="source-reader-more" onClick={() => setAfter((value) => value + LOAD_STEP)}>
                    Continue reading
                  </button>
                )
                : origin.corpus === "filing" ? "" : "End of source"}
        </div>
      </div>
    </section>
  );
}
