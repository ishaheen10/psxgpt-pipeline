import collectionsText from "../../../data/library-preview/collections.json?raw";
import documentsText from "../../../data/library-preview/documents.jsonl?raw";

interface RawCollection {
  collection_ref: string;
  publisher: string;
  display_name: string;
  description: string;
  why_tracked: string | null;
  coverage_note: string | null;
  cadence: string | null;
  status: string;
  landing_urls: string | string[];
  industry_tags: string | string[];
  signal_tags: string | string[];
  document_count: number;
  r2_document_count: number;
  parsed_document_count: number;
  retention_policy: RetentionLane;
  scope_status: "monitored" | "parked";
  retention_reason: string;
  initial_parse_document_count: number;
  archive_document_count: number;
  initial_parse_page_count: number;
  publication_cadence: string;
  freshness_status: "current" | "review" | "unknown";
  freshness_note: string;
  source_check_status: string;
  source_checked_at: string | null;
  latest_browse_date: string | null;
  latest_browse_year: number | null;
  browse_labeled_document_count: number;
  ambiguous_date_document_count: number;
}

interface RawDocument {
  document_id: string;
  collection_ref: string;
  source_title: string | null;
  title: string;
  document_type: string | null;
  published_date: string | null;
  document_date: string | null;
  year: number | null;
  year_confidence: string | null;
  browse_date: string | null;
  browse_year: number | null;
  browse_folder: string;
  browse_basis: string;
  browse_confidence: string;
  period_label: string | null;
  source_url: string;
  content_sha256: string;
  retention_policy: RetentionLane;
  scope_status: "monitored" | "parked";
  retention_reason: string;
  retention_lane: RetentionLane;
  initial_parse_selected: number;
  legal_status: "publisher_listed_active" | "unverified" | null;
  byte_size: number;
  original_r2_key: string;
  original_url: string | null;
  r2_available: number;
  page_count: number | null;
  parsed_pages: number;
  parse_coverage: "unparsed" | "partial" | "complete";
}

interface PreviewData {
  collections: Array<RawCollection & {
    landing_urls: string[];
    industry_tags: string[];
    signal_tags: string[];
    years: Array<{ year: number | null; label: string; document_count: number; r2_document_count: number }>;
  }>;
  documents: RawDocument[];
}

let cache: PreviewData | null = null;

type RetentionLane = "recent_series" | "active_register" | "governing_baseline" | "latest_edition" | "archive_only";
type RetentionView = "initial" | "archive" | "all";
const NO_STORE_HEADERS = { "Cache-Control": "no-store, max-age=0" };

function stringArray(value: string | string[]): string[] {
  if (Array.isArray(value)) return value;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

function loadPreviewData(): PreviewData {
  if (cache) return cache;
  const collections = JSON.parse(collectionsText) as RawCollection[];
  const documents = documentsText
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line) as RawDocument);
  const byCollection = new Map<string, Map<number | null, { document_count: number; r2_document_count: number }>>();
  for (const document of documents) {
    const years = byCollection.get(document.collection_ref) || new Map();
    const year = document.browse_year ?? null;
    const current = years.get(year) || { document_count: 0, r2_document_count: 0 };
    current.document_count += 1;
    current.r2_document_count += Number(document.r2_available || 0);
    years.set(year, current);
    byCollection.set(document.collection_ref, years);
  }
  cache = {
    documents,
    collections: collections.map((collection) => ({
      ...collection,
      landing_urls: stringArray(collection.landing_urls),
      industry_tags: stringArray(collection.industry_tags),
      signal_tags: stringArray(collection.signal_tags),
      years: [...(byCollection.get(collection.collection_ref) || new Map()).entries()]
        .sort(([left], [right]) => left == null ? 1 : right == null ? -1 : right - left)
        .map(([year, counts]) => ({ year, label: year == null ? "Undated" : String(year), ...counts })),
    })),
  };
  return cache;
}

function viewFromUrl(url: URL): RetentionView {
  const value = url.searchParams.get("view");
  return value === "archive" || value === "all" ? value : "initial";
}

function inView(document: RawDocument, view: RetentionView): boolean {
  if (view === "all") return true;
  return view === "initial" ? Boolean(document.initial_parse_selected) : !document.initial_parse_selected;
}

function summarize(documents: RawDocument[]) {
  const unique = new Map<string, RawDocument>();
  for (const document of documents) {
    const key = document.content_sha256 || document.document_id;
    const current = unique.get(key);
    if (!current || (!current.page_count && document.page_count)) unique.set(key, document);
  }
  const rows = [...unique.values()];
  return {
    document_records: documents.length,
    unique_pdfs: rows.length,
    pages: rows.reduce((sum, row) => sum + Number(row.page_count || 0), 0),
    pdfs_with_page_count: rows.reduce((sum, row) => sum + Number(Boolean(row.page_count)), 0),
  };
}

function collectionsForView(data: PreviewData, view: RetentionView) {
  const visible = data.documents.filter((document) => inView(document, view));
  const byCollection = new Map<string, RawDocument[]>();
  for (const document of visible) {
    byCollection.set(document.collection_ref, [...(byCollection.get(document.collection_ref) || []), document]);
  }
  return data.collections.flatMap((collection) => {
    const members = byCollection.get(collection.collection_ref) || [];
    if (!members.length) return [];
    const years = new Map<number | null, { document_count: number; r2_document_count: number }>();
    for (const document of members) {
      const year = document.browse_year ?? null;
      const current = years.get(year) || { document_count: 0, r2_document_count: 0 };
      current.document_count += 1;
      current.r2_document_count += Number(document.r2_available || 0);
      years.set(year, current);
    }
    return [{
      ...collection,
      view_document_count: members.length,
      years: [...years.entries()]
        .sort(([left], [right]) => left == null ? 1 : right == null ? -1 : right - left)
        .map(([year, counts]) => ({ year, label: year == null ? "Undated" : String(year), ...counts })),
    }];
  });
}

export async function GET(request: Request) {
  try {
    const data = loadPreviewData();
    const url = new URL(request.url);
    const view = viewFromUrl(url);
    const collectionRef = url.searchParams.get("collection_ref");
    if (!collectionRef) {
      const visibleDocuments = data.documents.filter((document) => inView(document, view));
      return Response.json({
        preview: true,
        view,
        collections: collectionsForView(data, view),
        totals: { collections: new Set(visibleDocuments.map((row) => row.collection_ref)).size, ...summarize(visibleDocuments) },
      }, { headers: NO_STORE_HEADERS });
    }
    const query = (url.searchParams.get("q") || "").trim().toLowerCase();
    const coverage = url.searchParams.get("parse_coverage") || "";
    const year = url.searchParams.get("year") || "";
    const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit") || 50)));
    const offset = Math.max(0, Number(url.searchParams.get("offset") || 0));
    const matching = data.documents.filter((document) => {
      if (document.collection_ref !== collectionRef) return false;
      if (!inView(document, view)) return false;
      if (year === "undated" && document.browse_year != null) return false;
      if (year && year !== "undated" && document.browse_year !== Number(year)) return false;
      if (coverage && document.parse_coverage !== coverage) return false;
      if (query && !`${document.title} ${document.source_title || ""}`.toLowerCase().includes(query)) return false;
      return true;
    });
    return Response.json(
      { documents: matching.slice(offset, offset + limit), total: matching.length, limit, offset, preview: true, view },
      { headers: NO_STORE_HEADERS },
    );
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : String(error) },
      { status: 500, headers: NO_STORE_HEADERS },
    );
  }
}
