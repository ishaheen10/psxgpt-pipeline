import type { CorpusKind, InclusionMode, StackedSource } from "./corpus";

/**
 * Named collections of clipped pages, persisted in the browser.
 *
 * Assembling context is not a single sitting. A researcher clips a few pages,
 * goes and reads something else, comes back, and expects the pile to still be
 * there. Losing it on refresh makes the tool unusable for real work.
 *
 * Only page identity and the researcher's own choices are stored — never the
 * page text. Full text is refetched on open (and cached in memory), which keeps
 * storage small, keeps a reopened scrapbook consistent with the corpus if a
 * page has been republished, and avoids duplicating the corpus into
 * localStorage.
 */

const STORAGE_KEY = "ledger.scrapbooks.v1";
const ACTIVE_KEY = "ledger.scrapbooks.active";

export interface ClippedPage {
  corpus: CorpusKind;
  id: number;
  entity: string;
  title: string;
  period: string;
  page_number: number | null;
  summary: string;
  bm25_score: number;
  source_url: string | null;
  asset_key: string | null;
  inclusionMode: InclusionMode;
}

export interface Scrapbook {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  prompt: string;
  pages: ClippedPage[];
}

function newId(): string {
  return `sb_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function emptyScrapbook(name = "New research session"): Scrapbook {
  const now = new Date().toISOString();
  return { id: newId(), name, createdAt: now, updatedAt: now, prompt: "", pages: [] };
}

function isBrowser(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

export function loadScrapbooks(): Scrapbook[] {
  if (!isBrowser()) return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    // Stored data is user-local but still untrusted input to this code path.
    return parsed.filter(
      (entry): entry is Scrapbook =>
        Boolean(entry) &&
        typeof (entry as Scrapbook).id === "string" &&
        Array.isArray((entry as Scrapbook).pages),
    );
  } catch {
    return [];
  }
}

export function saveScrapbooks(scrapbooks: Scrapbook[]): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(scrapbooks));
  } catch {
    // A full or blocked storage quota must not break the working session.
  }
}

export function loadActiveId(): string | null {
  if (!isBrowser()) return null;
  return window.localStorage.getItem(ACTIVE_KEY);
}

export function saveActiveId(id: string): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(ACTIVE_KEY, id);
  } catch {
    /* ignore */
  }
}

/** Reduces a working source to the fields worth persisting. */
export function toClipped(source: StackedSource): ClippedPage {
  return {
    corpus: source.corpus,
    id: source.id,
    entity: source.entity,
    title: source.title,
    period: source.period,
    page_number: source.page_number,
    summary: source.summary,
    bm25_score: source.bm25_score,
    source_url: source.source_url,
    asset_key: source.asset_key,
    inclusionMode: source.inclusionMode,
  };
}

/**
 * Rebuilds a working source from a stored clip. `detail` is intentionally
 * absent: the caller refetches it, and until then the source contributes its
 * indexed summary rather than pretending to hold full text it does not have.
 */
export function fromClipped(page: ClippedPage): StackedSource {
  return {
    id: page.id,
    corpus: page.corpus,
    entity: page.entity,
    source_name: page.entity,
    title: page.title,
    period: page.period,
    page_number: page.page_number,
    summary: page.summary,
    snippet: "",
    bm25_score: page.bm25_score,
    source_url: page.source_url,
    thumbnail_url: null,
    asset_key: page.asset_key,
    tags: [],
    quality_flags: [],
    loading: true,
    inclusionMode: page.inclusionMode,
  };
}

export function describeScrapbook(scrapbook: Scrapbook): string {
  const entities = [...new Set(scrapbook.pages.map((page) => page.entity))];
  if (scrapbook.pages.length === 0) return "empty";
  const who = entities.slice(0, 3).join(", ") + (entities.length > 3 ? "…" : "");
  return `${scrapbook.pages.length} page${scrapbook.pages.length === 1 ? "" : "s"} · ${who}`;
}

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days}d ago`;
}
