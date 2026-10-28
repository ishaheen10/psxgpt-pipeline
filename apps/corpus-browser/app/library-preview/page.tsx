"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

interface YearFolder { year: number | null; label: string; document_count: number; r2_document_count: number }
interface Collection {
  collection_ref: string; publisher: string; display_name: string; description: string;
  why_tracked: string | null; coverage_note: string | null; cadence: string | null; status: string;
  industry_tags: string[]; signal_tags: string[]; document_count: number; r2_document_count: number;
  parsed_document_count: number; years: YearFolder[];
  publication_cadence: string; freshness_status: "current" | "review" | "unknown";
  freshness_note: string; source_check_status: string; source_checked_at: string | null;
  latest_browse_date: string | null; latest_browse_year: number | null;
  browse_labeled_document_count: number; ambiguous_date_document_count: number;
  retention_policy: RetentionLane; scope_status: "monitored" | "parked"; retention_reason: string;
  initial_parse_document_count: number; archive_document_count: number; initial_parse_page_count: number;
  view_document_count: number;
}
interface LibraryDocument {
  document_id: string; source_title: string | null; title: string; published_date: string | null;
  document_date: string | null; year: number | null; year_confidence: string | null; source_url: string;
  browse_date: string | null; browse_year: number | null; browse_folder: string;
  browse_basis: string; browse_confidence: string; period_label: string | null;
  byte_size: number; original_r2_key: string; original_url: string | null; r2_available: number;
  page_count: number | null; parsed_pages: number; parse_coverage: "unparsed" | "partial" | "complete";
  retention_policy: RetentionLane; scope_status: "monitored" | "parked"; retention_reason: string;
  retention_lane: RetentionLane; initial_parse_selected: number;
  legal_status: "publisher_listed_active" | "unverified" | null;
}

type RetentionLane = "recent_series" | "active_register" | "governing_baseline" | "latest_edition" | "archive_only";
type RetentionView = "initial" | "archive" | "all";
interface LibraryTotals { collections: number; document_records: number; unique_pdfs: number; pages: number; pdfs_with_page_count: number }

const PAGE_SIZE = 50;
const RETENTION_CATALOG_VERSION = "retention-v1";
const PREFERRED_COLLECTIONS = [
  "pk_ogra.notifications", "pk_mnfsr_nfdc.monthly_fertilizer_reviews", "pk_ccp.commission_orders",
  "pk_pakistan_code.active_federal_laws", "pk_ppra.annual_reports", "pk_pta.annual_reports", "pk_port_qasim_authority.daily_shipping_and_cargo_handling_reports",
];

const VIEW_COPY: Record<RetentionView, { label: string; description: string }> = {
  initial: { label: "Initial parse selection", description: "Recent releases plus governing baselines, active registers and current-use editions." },
  archive: { label: "Archive only", description: "Older or parked material remains browseable and can be parsed later." },
  all: { label: "All downloaded PDFs", description: "The complete downloaded library before retention routing." },
};

const LANE_LABELS: Record<RetentionLane, string> = {
  recent_series: "Recent series",
  active_register: "Publisher active register",
  governing_baseline: "Governing baseline",
  latest_edition: "Latest/current edition",
  archive_only: "Archive only",
};

function formatBytes(value: number): string {
  if (!value) return "Unknown size";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

export default function LibraryPreviewPage() {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [totals, setTotals] = useState<LibraryTotals>({ collections: 0, document_records: 0, unique_pdfs: 0, pages: 0, pdfs_with_page_count: 0 });
  const [retentionView, setRetentionView] = useState<RetentionView>("initial");
  const [selectedRef, setSelectedRef] = useState("");
  const [selectedYear, setSelectedYear] = useState("");
  const [documents, setDocuments] = useState<LibraryDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`/api/library-preview?view=${retentionView}&catalog=${RETENTION_CATALOG_VERSION}`, { cache: "no-store" })
      .then(async (response) => { if (!response.ok) throw new Error("Could not read the local library artifacts."); return response.json(); })
      .then((payload) => {
        const rows = (payload.collections || []) as Collection[];
        setCollections(rows);
        setTotals(payload.totals || {});
        const requestedRef = typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("collection_ref") || "";
        setSelectedRef((current) => requestedRef && rows.some((row) => row.collection_ref === requestedRef)
          ? requestedRef
          : rows.some((row) => row.collection_ref === current)
            ? current
            : PREFERRED_COLLECTIONS.find((ref) => rows.some((row) => row.collection_ref === ref)) || rows[0]?.collection_ref || "");
        setSelectedYear("");
        setOffset(0);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, [retentionView]);

  useEffect(() => {
    if (!selectedRef) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ collection_ref: selectedRef, limit: String(PAGE_SIZE), offset: String(offset) });
    params.set("view", retentionView);
    params.set("catalog", RETENTION_CATALOG_VERSION);
    if (selectedYear) params.set("year", selectedYear);
    if (query.trim()) params.set("q", query.trim());
    fetch(`/api/library-preview?${params}`, { signal: controller.signal, cache: "no-store" })
      .then(async (response) => { if (!response.ok) throw new Error("Could not load this local folder."); return response.json(); })
      .then((payload) => { setDocuments(payload.documents || []); setTotal(payload.total || 0); })
      .catch((reason) => { if (reason?.name !== "AbortError") setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [selectedRef, selectedYear, query, offset, retentionView]);

  const selected = useMemo(() => collections.find((row) => row.collection_ref === selectedRef) || null, [collections, selectedRef]);
  const publisherGroups = useMemo(() => {
    const groups = new Map<string, Collection[]>();
    for (const collection of collections) groups.set(collection.publisher, [...(groups.get(collection.publisher) || []), collection]);
    return [...groups.entries()];
  }, [collections]);

  return (
    <main className="app-shell library-shell">
      <header className="app-header">
        <Link className="brand-lockup" href="/" aria-label="Open Ledger research"><span className="brand-mark">L</span><span><strong>Ledger</strong><small>Source workbench</small></span></Link>
        <div className="header-actions"><Link className="library-nav-link" href="/context-map">Context map</Link><span className="library-nav-link active">PDF archive</span><Link className="library-nav-link" href="/industries">Pack detail</Link></div>
      </header>
      <section className="library-intro">
        <span className="kicker">Pre-upload review · local artifacts only</span>
        <h1>Pakistan’s publications, arranged as folders.</h1>
        <p>Use retention as a view, not a deletion rule. The initial parse selection keeps recent releases and older governing material; every downloaded PDF remains available in the archive.</p>
        <div className="retention-view-tabs" role="group" aria-label="Retention view">
          {(Object.entries(VIEW_COPY) as Array<[RetentionView, { label: string; description: string }]>).map(([value, copy]) => <button key={value} type="button" className={retentionView === value ? "selected" : ""} onClick={() => setRetentionView(value)}><strong>{copy.label}</strong><span>{copy.description}</span></button>)}
        </div>
        <div className="library-stat-row"><span><strong>{totals.unique_pdfs.toLocaleString()}</strong> unique PDFs</span><span><strong>{totals.pages.toLocaleString()}</strong> pages</span><span><strong>{totals.collections.toLocaleString()}</strong> publication folders</span><span><strong>{retentionView === "initial" ? "2024+ and durable" : retentionView === "archive" ? "retained, not selected" : "complete inventory"}</strong></span></div>
        {totals.pdfs_with_page_count < totals.unique_pdfs && <p className="page-count-note">Page total covers {totals.pdfs_with_page_count.toLocaleString()} of {totals.unique_pdfs.toLocaleString()} unique PDFs with resolved page counts.</p>}
      </section>
      <section className="library-workspace">
        <aside className="library-tree" aria-label="Publication folders">
          <div className="library-tree-title"><span>{VIEW_COPY[retentionView].label}</span><small>{collections.length} folders</small></div>
          {loading ? <p className="library-muted">Loading local catalog…</p> : publisherGroups.map(([publisher, rows]) => (
            <details key={publisher} open={rows.some((row) => row.collection_ref === selectedRef)}>
              <summary>{publisher}<small>{rows.reduce((sum, row) => sum + row.view_document_count, 0).toLocaleString()}</small></summary>
              <div>{rows.map((row) => (
                <div className="collection-folder" key={row.collection_ref}>
                  <button type="button" className={row.collection_ref === selectedRef && !selectedYear ? "selected" : ""} onClick={() => { setSelectedRef(row.collection_ref); setSelectedYear(""); setOffset(0); }}><span><i aria-hidden="true">▰</i>{row.display_name}</span><small>{row.view_document_count.toLocaleString()}</small></button>
                  {row.collection_ref === selectedRef && <div className="year-folders">{row.years.map((year) => {
                    const value = year.year == null ? "undated" : String(year.year);
                    return <button key={value} type="button" className={selectedYear === value ? "selected" : ""} onClick={() => { setSelectedYear(value); setOffset(0); }}><span><i aria-hidden="true">▰</i>{year.label}</span><small>{year.document_count}</small></button>;
                  })}</div>}
                </div>
              ))}</div>
            </details>
          ))}
        </aside>
        <section className="library-content">
          {error && <div className="library-error">{error}</div>}
          {selected && <>
            <nav className="folder-breadcrumb" aria-label="Current folder"><span>Pakistan</span><b>/</b><span>{selected.publisher}</span><b>/</b><span>{selected.display_name}</span>{selectedYear && <><b>/</b><strong>{selectedYear === "undated" ? "Undated" : selectedYear}</strong></>}</nav>
            <section className="collection-card">
              <div className="collection-heading"><div><span>{selected.publisher}</span><h2>{selected.display_name}</h2></div><span className={`coverage-pill ${selected.freshness_status}`}>{selected.freshness_status === "current" ? "source current" : "review archive"}</span></div>
              <p>{selected.description}</p>
              {selected.why_tracked && <div className="why-tracked"><strong>Why it is here</strong><span>{selected.why_tracked}</span></div>}
              {selected.coverage_note && <div className="coverage-note"><strong>Coverage note</strong><span>{selected.coverage_note}</span></div>}
              <div className={`retention-note ${selected.scope_status}`}><strong>{selected.scope_status === "parked" ? "Why parked" : "Retention rule"}</strong><span>{selected.retention_reason}</span></div>
              <div className="coverage-note"><strong>Freshness assessment</strong><span>{selected.freshness_note}</span></div>
              <div className="collection-connections"><div><strong>Retention policy</strong><span>{LANE_LABELS[selected.retention_policy]}</span></div><div><strong>Publication cadence</strong><span>{selected.publication_cadence.replaceAll("_", " ")}</span></div><div><strong>URL check cadence</strong><span>{selected.cadence?.replaceAll("_", " ") || "Not established"}</span></div><div><strong>Official source checked</strong><span>{selected.source_checked_at ? new Date(selected.source_checked_at).toLocaleDateString() : "Not established"}</span></div><div><strong>Industries connected</strong><span>{selected.industry_tags.join(" · ") || "Macro / cross-sector"}</span></div><div><strong>Selection split</strong><span>{selected.initial_parse_document_count.toLocaleString()} selected · {selected.archive_document_count.toLocaleString()} archive</span></div><div><strong>Browse path</strong><span>Pakistan / {selected.publisher} / {selected.display_name} / Publication year</span></div><div><strong>Date labels</strong><span>{selected.browse_labeled_document_count.toLocaleString()} labeled · {selected.ambiguous_date_document_count.toLocaleString()} need caution</span></div></div>
            </section>
            <div className="library-controls"><input value={query} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} placeholder="Filter titles in this folder…"/><span>{selectedYear ? `${selectedYear === "undated" ? "Undated" : selectedYear} · ` : ""}{total.toLocaleString()} PDFs</span></div>
            <div className="document-list">{documents.map((document) => (
              <article className="library-document" key={document.document_id}>
                <div className="document-icon">PDF</div>
                <div className="document-copy"><span className="document-title">{document.title}</span><div><span>{document.browse_date || document.browse_year || "Date not established"}</span>{document.period_label && <span>Period {document.period_label}</span>}<span>{document.browse_basis.replaceAll("_", " ")} · {document.browse_confidence}</span><span>{formatBytes(document.byte_size)}</span>{document.page_count && <span>{document.page_count.toLocaleString()} pages</span>}<span>{document.parse_coverage.replaceAll("_", " ")}</span></div><code className="document-path">Stored object: {document.original_r2_key}</code>{document.source_title && document.source_title !== document.title && <small className="source-filename">Original label: {document.source_title}</small>}</div>
                <div className="document-actions"><span className={`retention-badge ${document.retention_lane}`}>{LANE_LABELS[document.retention_lane]}</span>{document.legal_status === "unverified" && <span className="legal-status">Legal status unverified</span>}<a href={document.source_url} target="_blank" rel="noreferrer">Official source</a></div>
              </article>
            ))}{!documents.length && <div className="library-empty">No PDFs match this folder filter.</div>}</div>
            <div className="library-pagination"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><span>{total ? `${offset + 1}–${Math.min(total, offset + PAGE_SIZE)} of ${total.toLocaleString()}` : "0 documents"}</span><button type="button" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div>
          </>}
        </section>
      </section>
    </main>
  );
}
