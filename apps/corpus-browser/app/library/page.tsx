"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import AuthGate from "../components/AuthGate";
import { authenticatedFetch, useLedgerAuth } from "../lib/auth-client";

interface Collection {
  collection_ref: string;
  publisher: string;
  display_name: string;
  description: string;
  why_tracked: string | null;
  coverage_note: string | null;
  cadence: string | null;
  status: string;
  landing_urls: string[];
  industry_tags: string[];
  signal_tags: string[];
  document_count: number;
  r2_document_count: number;
  parsed_document_count: number;
  years: Array<{
    year: number | null;
    label: string;
    document_count: number;
    r2_document_count: number;
  }>;
}

interface LibraryDocument {
  document_id: string;
  source_title: string | null;
  title: string;
  document_type: string | null;
  published_date: string | null;
  document_date: string | null;
  year: number | null;
  year_confidence: string | null;
  source_url: string;
  byte_size: number;
  original_url: string | null;
  r2_available: number;
  page_count: number | null;
  parsed_pages: number;
  parse_coverage: "unparsed" | "partial" | "complete";
}

interface DocumentsResponse {
  documents: LibraryDocument[];
  total: number;
  limit: number;
  offset: number;
}

const PAGE_SIZE = 50;

function formatBytes(value: number): string {
  if (!value) return "Unknown size";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function documentDate(document: LibraryDocument): string {
  return document.published_date || document.document_date || (document.year ? String(document.year) : "Date not established");
}

export default function LibraryPage() {
  const auth = useLedgerAuth();
  const [collections, setCollections] = useState<Collection[]>([]);
  const [selectedRef, setSelectedRef] = useState("");
  const [selectedYear, setSelectedYear] = useState("");
  const [documents, setDocuments] = useState<LibraryDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [coverage, setCoverage] = useState("");
  const [loading, setLoading] = useState(true);
  const [documentLoading, setDocumentLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!auth.user) return;
    authenticatedFetch("/api/library/collections", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("The PDF library is not published yet.");
        return response.json() as Promise<{ collections: Collection[] }>;
      })
      .then((payload) => {
        setCollections(payload.collections || []);
        setSelectedRef((current) => current || payload.collections?.[0]?.collection_ref || "");
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, [auth.user]);

  useEffect(() => {
    if (!selectedRef || !auth.user) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setDocumentLoading(true);
      const params = new URLSearchParams({
        collection_ref: selectedRef,
        limit: String(PAGE_SIZE),
        offset: String(offset),
      });
      if (query.trim()) params.set("q", query.trim());
      if (coverage) params.set("parse_coverage", coverage);
      if (selectedYear) params.set("year", selectedYear);
      authenticatedFetch(`/api/library/documents?${params}`, { signal: controller.signal, cache: "no-store" })
        .then(async (response) => {
          if (!response.ok) throw new Error("Could not load this publication folder.");
          return response.json() as Promise<DocumentsResponse>;
        })
        .then((payload) => {
          setDocuments(payload.documents || []);
          setTotal(payload.total || 0);
        })
        .catch((reason) => {
          if (reason?.name !== "AbortError") setError(reason instanceof Error ? reason.message : String(reason));
        })
        .finally(() => setDocumentLoading(false));
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [auth.user, selectedRef, selectedYear, query, coverage, offset]);

  const selected = useMemo(
    () => collections.find((collection) => collection.collection_ref === selectedRef) || null,
    [collections, selectedRef],
  );
  const publisherGroups = useMemo(() => {
    const groups = new Map<string, Collection[]>();
    for (const collection of collections) {
      const group = groups.get(collection.publisher) || [];
      group.push(collection);
      groups.set(collection.publisher, group);
    }
    return [...groups.entries()];
  }, [collections]);

  if (auth.loading || !auth.user) {
    return (
      <AuthGate
        loading={auth.loading}
        configured={auth.configured}
        error={auth.error}
        onGoogle={auth.signInWithGoogle}
        onMagicLink={auth.sendMagicLink}
      />
    );
  }

  return (
    <main className="app-shell library-shell">
      <header className="app-header">
        <Link className="brand-lockup" href="/" aria-label="Open Ledger research">
          <span className="brand-mark">L</span>
          <span><strong>Ledger</strong><small>Source workbench</small></span>
        </Link>
        <div className="header-actions">
          <Link className="library-nav-link active" href="/library">PDF library</Link>
          <Link className="library-nav-link" href="/">Search & research</Link>
          <button type="button" className="sign-out" onClick={() => void auth.signOut()}>Sign out</button>
        </div>
      </header>

      <section className="library-intro">
        <span className="kicker">Cloudflare source library</span>
        <h1>Pakistan’s publications, arranged as folders.</h1>
        <p>
          Browse from publisher to publication stream to year. Every available file opens the original,
          unsplit PDF stored in Cloudflare; the official publisher URL remains attached as provenance.
        </p>
        <div className="library-stat-row">
          <span><strong>{collections.length.toLocaleString()}</strong> publication folders</span>
          <span><strong>{collections.reduce((sum, row) => sum + Number(row.document_count), 0).toLocaleString()}</strong> original PDFs</span>
          <span><strong>{collections.reduce((sum, row) => sum + Number(row.r2_document_count), 0).toLocaleString()}</strong> mirrored to Cloudflare</span>
        </div>
      </section>

      <section className="library-workspace">
        <aside className="library-tree" aria-label="Publication folders">
          <div className="library-tree-title"><span>Pakistan</span><small>{collections.length} folders</small></div>
          {loading ? <p className="library-muted">Loading library…</p> : publisherGroups.map(([publisher, rows]) => (
            <details key={publisher} open={rows.some((row) => row.collection_ref === selectedRef)}>
              <summary>{publisher}<small>{rows.reduce((sum, row) => sum + Number(row.document_count), 0).toLocaleString()}</small></summary>
              <div>
                {rows.map((row) => (
                  <div className="collection-folder" key={row.collection_ref}>
                    <button
                      type="button"
                      className={row.collection_ref === selectedRef && !selectedYear ? "selected" : ""}
                      onClick={() => { setSelectedRef(row.collection_ref); setSelectedYear(""); setOffset(0); }}
                    >
                      <span><i aria-hidden="true">▰</i>{row.display_name}</span><small>{Number(row.document_count).toLocaleString()}</small>
                    </button>
                    {row.collection_ref === selectedRef && (
                      <div className="year-folders">
                        {row.years.map((year) => {
                          const value = year.year == null ? "undated" : String(year.year);
                          return (
                            <button
                              key={value}
                              type="button"
                              className={selectedYear === value ? "selected" : ""}
                              onClick={() => { setSelectedYear(value); setOffset(0); }}
                            >
                              <span><i aria-hidden="true">▰</i>{year.label}</span><small>{year.document_count}</small>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </details>
          ))}
        </aside>

        <section className="library-content">
          {error && <div className="library-error">{error}</div>}
          {selected && (
            <>
              <nav className="folder-breadcrumb" aria-label="Current folder">
                <span>Pakistan</span><b>/</b><span>{selected.publisher}</span><b>/</b><span>{selected.display_name}</span>
                {selectedYear && <><b>/</b><strong>{selectedYear === "undated" ? "Undated" : selectedYear}</strong></>}
              </nav>
              <section className="collection-card">
                <div className="collection-heading">
                  <div>
                    <span>{selected.publisher}</span>
                    <h2>{selected.display_name}</h2>
                  </div>
                  <span className={`coverage-pill ${selected.status}`}>{selected.status.replaceAll("_", " ")}</span>
                </div>
                <p>{selected.description}</p>
                {selected.why_tracked && <div className="why-tracked"><strong>Why it is here</strong><span>{selected.why_tracked}</span></div>}
                {selected.coverage_note && <div className="coverage-note"><strong>Coverage note</strong><span>{selected.coverage_note}</span></div>}
                <div className="collection-connections">
                  <div><strong>URL check cadence</strong><span>{selected.cadence?.replaceAll("_", " ") || "Not established"}</span></div>
                  <div><strong>Industries connected</strong><span>{selected.industry_tags.join(" · ") || "Macro / cross-sector"}</span></div>
                  <div><strong>Cloudflare folder</strong><span>{Number(selected.r2_document_count).toLocaleString()} of {Number(selected.document_count).toLocaleString()} originals mirrored</span></div>
                </div>
              </section>

              <div className="library-controls">
                <input value={query} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} placeholder="Filter titles in this folder…" />
                <select value={coverage} onChange={(event) => { setCoverage(event.target.value); setOffset(0); }}>
                  <option value="">Any parse state</option>
                  <option value="complete">Fully parsed</option>
                  <option value="partial">Partially parsed</option>
                  <option value="unparsed">Not parsed</option>
                </select>
                <span>{selectedYear ? `${selectedYear === "undated" ? "Undated" : selectedYear} · ` : ""}{total.toLocaleString()} PDFs</span>
              </div>

              <div className="document-list" aria-busy={documentLoading}>
                {documents.map((document) => {
                  return (
                    <article className="library-document" key={document.document_id}>
                      <div className="document-icon">PDF</div>
                      <div className="document-copy">
                        {document.original_url
                          ? <a href={document.original_url} target="_blank" rel="noreferrer">{document.title}</a>
                          : <span className="document-title">{document.title}</span>}
                        <div>
                          <span>{documentDate(document)}</span>
                          <span>{formatBytes(Number(document.byte_size))}</span>
                          {document.page_count && <span>{document.page_count.toLocaleString()} pages</span>}
                          {document.year_confidence && <span>Date confidence: {document.year_confidence}</span>}
                        </div>
                      </div>
                      <div className="document-actions">
                        <span className={`storage-state ${document.original_url ? "available" : "pending"}`}>
                          {document.original_url ? "In Cloudflare" : "Mirroring"}
                        </span>
                        {document.original_url && <a href={document.original_url} target="_blank" rel="noreferrer">Open PDF</a>}
                        <a href={document.source_url} target="_blank" rel="noreferrer">Official source</a>
                      </div>
                    </article>
                  );
                })}
                {!documentLoading && documents.length === 0 && <div className="library-empty">No PDFs match this folder filter.</div>}
              </div>

              <div className="library-pagination">
                <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
                <span>{total ? `${offset + 1}–${Math.min(total, offset + PAGE_SIZE)} of ${total.toLocaleString()}` : "0 documents"}</span>
                <button type="button" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
              </div>
            </>
          )}
        </section>
      </section>
    </main>
  );
}
