"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import contextMap from "../../data/context-map.json";

type Entrance = "industries" | "changes" | "publishers" | "explore";
type ConnectionFilter = "all" | "mapped" | "unmapped";

const DRIVER_LABELS: Record<string, string> = Object.fromEntries(contextMap.drivers.map((driver) => [driver.id, driver.label]));
const STATUS_LABELS: Record<string, string> = {
  parse_candidate: "Captured",
  metadata_monitor: "Metadata tracked",
  blocked: "Access blocked",
  source_gap: "Source gap",
  partial_source_coverage: "Partial",
  route_probation: "Route under observation",
  on_demand: "On demand",
  unresolved: "Needs review",
};
function readable(value: string | null | undefined) {
  return value ? value.replaceAll("_", " ") : "Not established";
}

function compact(value: number) {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function displayDate(value: string) {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

export default function ContextMapPage() {
  const [entrance, setEntrance] = useState<Entrance>("industries");
  const [changeIndustryId, setChangeIndustryId] = useState("all");
  const initialIndustry = contextMap.industries.find((industry) => industry.id === "fertilizer") || contextMap.industries[0];
  const [industryId, setIndustryId] = useState(initialIndustry.id);
  const [driverId, setDriverId] = useState("inputs");
  const industry = contextMap.industries.find((item) => item.id === industryId) || initialIndustry;
  const driverSignals = useMemo(() => industry.signals.filter((signal) => signal.driver_ids.includes(driverId)), [industry, driverId]);
  const [signalId, setSignalId] = useState(driverSignals[0]?.id || industry.signals[0]?.id || "");
  const visibleSignals = driverSignals.length ? driverSignals : industry.signals;
  const selectedSignal = visibleSignals.find((signal) => signal.id === signalId) || visibleSignals[0] || industry.signals[0];
  const collectionByRef = useMemo(() => new Map(contextMap.collections.map((collection) => [collection.collection_ref, collection])), []);
  const mappedCollectionRefs = useMemo(() => new Set(contextMap.industries.flatMap((item) => item.signals.flatMap((signal) => signal.collection_refs))), []);
  const reviewedIndustriesByCollection = useMemo(() => {
    const index = new Map<string, string[]>();
    for (const item of contextMap.industries) {
      for (const ref of new Set(item.signals.flatMap((signal) => signal.collection_refs))) {
        index.set(ref, [...new Set([...(index.get(ref) || []), item.name])]);
      }
    }
    return index;
  }, []);
  const changeIndustrySummaries = useMemo(() => contextMap.industries.map((item) => {
    const refs = new Set(item.signals.flatMap((signal) => signal.collection_refs));
    const documents = contextMap.recent_documents.filter((document) => refs.has(document.collection_ref));
    return {
      id: item.id,
      name: item.name,
      documents,
      publishers: new Set(documents.map((document) => document.publisher)).size,
      signals: item.signals.filter((signal) => signal.collection_refs.some((ref) => documents.some((document) => document.collection_ref === ref))).length,
      latest: documents.map((document) => document.browse_date).sort().reverse()[0] || null,
    };
  }).sort((left, right) => right.documents.length - left.documents.length || left.name.localeCompare(right.name)), []);
  const selectedChangeIndustry = contextMap.industries.find((item) => item.id === changeIndustryId) || null;
  const selectedChangeGroups = useMemo(() => {
    if (!selectedChangeIndustry) return [];
    const refs = [...new Set(selectedChangeIndustry.signals.flatMap((signal) => signal.collection_refs))];
    return refs.map((ref) => ({
      collection: collectionByRef.get(ref),
      signals: selectedChangeIndustry.signals.filter((signal) => signal.collection_refs.includes(ref)),
      documents: contextMap.recent_documents.filter((document) => document.collection_ref === ref),
    })).filter((group) => group.collection && group.documents.length)
      .sort((left, right) => String(right.documents[0]?.browse_date || "").localeCompare(String(left.documents[0]?.browse_date || "")));
  }, [collectionByRef, selectedChangeIndustry]);
  const selectedCollections = (selectedSignal?.collection_refs || []).map((ref) => collectionByRef.get(ref)).filter(Boolean);

  const allPublishers = useMemo(() => contextMap.institution_groups.flatMap((group) => group.publishers.map((publisher) => ({ ...publisher, group: group.name }))), []);
  const preferredPublisher = allPublishers.find((publisher) => publisher.id === "pk_sbp") || allPublishers[0];
  const [publisherId, setPublisherId] = useState(preferredPublisher?.id || "");
  const selectedPublisher = allPublishers.find((publisher) => publisher.id === publisherId) || preferredPublisher;
  const publisherCollections = (selectedPublisher?.collection_refs || []).map((ref) => collectionByRef.get(ref)).filter(Boolean);
  const publisherReviewedIndustries = [...new Set((selectedPublisher?.collection_refs || []).flatMap((ref) => reviewedIndustriesByCollection.get(ref) || []))].sort();
  const [publisherConnectionFilter, setPublisherConnectionFilter] = useState<ConnectionFilter>("all");
  const visiblePublisherCollections = publisherCollections.filter((collection) => {
    if (!collection || publisherConnectionFilter === "all") return Boolean(collection);
    return publisherConnectionFilter === "mapped" ? mappedCollectionRefs.has(collection.collection_ref) : !mappedCollectionRefs.has(collection.collection_ref);
  });

  const [exploreQuery, setExploreQuery] = useState("");
  const [exploreConnection, setExploreConnection] = useState<ConnectionFilter>("all");
  const [exploreScope, setExploreScope] = useState("monitored");
  const [exploreRetention, setExploreRetention] = useState("all");
  const [trackedRefs, setTrackedRefs] = useState<string[]>([]);

  useEffect(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem("ledger-tracked-collections") || "[]");
      if (Array.isArray(stored)) setTrackedRefs(stored.filter((item): item is string => typeof item === "string"));
    } catch {
      setTrackedRefs([]);
    }
  }, []);

  const exploredCollections = useMemo(() => {
    const query = exploreQuery.trim().toLowerCase();
    return contextMap.collections.filter((collection) => {
      const mapped = mappedCollectionRefs.has(collection.collection_ref);
      if (exploreConnection === "mapped" && !mapped) return false;
      if (exploreConnection === "unmapped" && mapped) return false;
      if (exploreScope !== "all" && collection.scope_status !== exploreScope) return false;
      if (exploreRetention !== "all" && collection.retention_policy !== exploreRetention) return false;
      if (!query) return true;
      return [collection.publisher, collection.display_name, collection.description, collection.why_tracked, ...collection.industry_tags]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(query);
    }).sort((left, right) => left.publisher.localeCompare(right.publisher) || left.display_name.localeCompare(right.display_name));
  }, [exploreConnection, exploreQuery, exploreRetention, exploreScope, mappedCollectionRefs]);

  function toggleTracked(collectionRef: string) {
    setTrackedRefs((current) => {
      const next = current.includes(collectionRef) ? current.filter((ref) => ref !== collectionRef) : [...current, collectionRef];
      window.localStorage.setItem("ledger-tracked-collections", JSON.stringify(next));
      return next;
    });
  }

  function chooseIndustry(id: string) {
    const next = contextMap.industries.find((item) => item.id === id);
    if (!next) return;
    setIndustryId(id);
    const nextDriver = contextMap.drivers.find((driver) => next.signals.some((signal) => signal.driver_ids.includes(driver.id)))?.id || "inputs";
    setDriverId(nextDriver);
    setSignalId(next.signals.find((signal) => signal.driver_ids.includes(nextDriver))?.id || next.signals[0]?.id || "");
  }

  function chooseDriver(id: string) {
    setDriverId(id);
    setSignalId(industry.signals.find((signal) => signal.driver_ids.includes(id))?.id || industry.signals[0]?.id || "");
  }

  return (
    <main className="context-map-shell">
      <header className="app-header context-map-app-header">
        <Link className="brand-lockup" href="/" aria-label="Open Ledger research"><span className="brand-mark">L</span><span><strong>Ledger</strong><small>Source workbench</small></span></Link>
        <nav className="context-top-nav" aria-label="Authoritative corpus views">
          <Link href="/context-map" className="active">Context map</Link>
          <Link href="/source-graph">Source graph</Link>
          <Link href="/library-preview">PDF archive</Link>
          <Link href="/industries">Pack detail</Link>
        </nav>
      </header>

      <section className="context-map-hero">
        <span className="kicker">Pakistan authoritative context</span>
        <h1>Follow the question to the source.</h1>
        <p>Start with an industry or institution, see which operating signal a publication observes, then open the exact provenance folder. Documents remain leaves of the map—not thousands of competing nodes.</p>
        <div className="context-map-stats">
          <span><strong>{contextMap.summary.industries}</strong> industries</span>
          <span><strong>{contextMap.summary.signals}</strong> signals</span>
          <span><strong>{contextMap.summary.connected_collections}</strong> connected collections</span>
          <span><strong>{contextMap.summary.publishers}</strong> publishers</span>
        </div>
      </section>

      <nav className="context-entrances" aria-label="Choose how to explore">
        <button type="button" className={entrance === "industries" ? "selected" : ""} onClick={() => setEntrance("industries")}><strong>Industries</strong><span>Operating drivers → signals → sources</span></button>
        <button type="button" className={entrance === "changes" ? "selected" : ""} onClick={() => setEntrance("changes")}><strong>What changed?</strong><span>Industry → signal → latest releases</span></button>
        <button type="button" className={entrance === "publishers" ? "selected" : ""} onClick={() => setEntrance("publishers")}><strong>Publishers</strong><span>Institution → collections → connections</span></button>
        <button type="button" className={entrance === "explore" ? "selected" : ""} onClick={() => setEntrance("explore")}><strong>Explore all sources</strong><span>Mapped, unmapped and parked material</span></button>
      </nav>

      {entrance === "industries" && (
        <section className="context-map-workspace">
          <aside className="context-industry-rail">
            <div><strong>Choose an industry</strong><span>{contextMap.industries.length} operating models</span></div>
            {contextMap.industries.map((item) => <button type="button" key={item.id} className={item.id === industry.id ? "selected" : ""} onClick={() => chooseIndustry(item.id)}><span>{item.name}</span><small>{item.signals.length}</small></button>)}
          </aside>
          <div className="context-map-main">
            <header className="context-industry-intro">
              <span>Industry operating model</span>
              <h2>{industry.name}</h2>
              <p>{industry.economic_identity}</p>
              <div className="context-operating-line"><div><strong>Inputs</strong><span>{industry.inputs.join(" · ") || "Company-specific"}</span></div><b>→</b><div><strong>Industry engine</strong><span>{industry.state_variables.join(" · ")}</span></div><b>→</b><div><strong>Outputs</strong><span>{industry.outputs.join(" · ") || "Company-specific"}</span></div></div>
            </header>

            <section className="context-path" aria-label={`${industry.name} source path`}>
              <div className="context-path-stage">
                <header><span>01</span><div><strong>Operating driver</strong><small>What can change the view?</small></div></header>
                <div className="context-stage-options">{contextMap.drivers.map((driver) => {
                  const count = industry.signals.filter((signal) => signal.driver_ids.includes(driver.id)).length;
                  return <button type="button" key={driver.id} disabled={!count} className={driver.id === driverId ? "selected" : ""} onClick={() => chooseDriver(driver.id)}><strong>{driver.label}</strong><span>{count ? `${count} signals` : "No mapped signal"}</span></button>;
                })}</div>
                <p className="context-stage-question">{contextMap.drivers.find((driver) => driver.id === driverId)?.question}</p>
              </div>

              <div className="context-path-stage">
                <header><span>02</span><div><strong>Observable signal</strong><small>What evidence would move the answer?</small></div></header>
                <div className="context-stage-options">{visibleSignals.map((signal) => <button type="button" key={signal.id} className={signal.id === selectedSignal?.id ? "selected" : ""} onClick={() => setSignalId(signal.id)}><strong>{signal.name}</strong><span>{STATUS_LABELS[signal.deployment_state] || readable(signal.deployment_state)}</span></button>)}</div>
              </div>

              <div className="context-path-stage context-signal-detail">
                <header><span>03</span><div><strong>Why it matters</strong><small>The judgment connecting signal to industry</small></div></header>
                {selectedSignal ? <><p>{selectedSignal.why}</p><dl><div><dt>Measures</dt><dd>{selectedSignal.measures.join(" · ") || "Event or standing context"}</dd></div><div><dt>Expected release</dt><dd>{selectedSignal.release_frequency} · lag {selectedSignal.expected_lag}</dd></div><div><dt>Driver</dt><dd>{selectedSignal.driver_ids.map((id) => DRIVER_LABELS[id] || id).join(" · ")}</dd></div></dl></> : <p>No signal has been mapped to this driver yet.</p>}
              </div>

              <div className="context-path-stage context-collection-stage">
                <header><span>04</span><div><strong>Authoritative collections</strong><small>Open the provenance folder and its documents</small></div></header>
                <div className="context-collection-list">{selectedCollections.map((collection) => collection && <article key={collection.collection_ref}><span>{collection.publisher}</span><h3>{collection.display_name}</h3><p>{collection.description}</p><div><small>{readable(collection.cadence)}</small><small>{compact(collection.initial_parse_document_count)} selected PDFs</small><small>{readable(collection.retention_policy)}</small></div><Link href={`/library-preview?collection_ref=${encodeURIComponent(collection.collection_ref)}&view=initial`}>Open source folder →</Link></article>)}{!selectedCollections.length && <p className="context-empty">This signal is known, but a captured collection has not yet been registered.</p>}</div>
              </div>
            </section>

            <section className="context-key-questions"><header><span>Questions that change the view</span><Link href={`/industries#${industry.id}`}>Open the full industry pack →</Link></header><ul>{industry.key_questions.map((question) => <li key={question}>{question}</li>)}</ul>{industry.known_gaps.length > 0 && <p><strong>Known gap:</strong> {industry.known_gaps.join(" ")}</p>}</section>
          </div>
        </section>
      )}

      {entrance === "changes" && (
        <section className="context-changes-view">
          <header>
            <span className="kicker">Authoritative publication activity</span>
            <h2>{selectedChangeIndustry ? `What changed around ${selectedChangeIndustry.name}?` : "What changed across industries?"}</h2>
            <p>These are newly published authoritative documents grouped by the industry signal they may update—not yet a claim that underlying policy or economics changed. Substantive change detection begins after parsing. {contextMap.summary.future_dated_documents_excluded > 0 && `${contextMap.summary.future_dated_documents_excluded.toLocaleString()} future-dated metadata exceptions are excluded.`}</p>
          </header>

          <div className="context-change-industry-picker" role="group" aria-label="Choose an industry for publication activity">
            <button type="button" className={changeIndustryId === "all" ? "selected" : ""} onClick={() => setChangeIndustryId("all")}><strong>All industries</strong><span>{contextMap.recent_documents.length} recent documents in the activity sample</span></button>
            {changeIndustrySummaries.map((summary) => <button type="button" key={summary.id} className={changeIndustryId === summary.id ? "selected" : ""} onClick={() => setChangeIndustryId(summary.id)}><strong>{summary.name}</strong><span>{summary.documents.length} {summary.documents.length === 1 ? "release" : "releases"} · {summary.signals} {summary.signals === 1 ? "signal" : "signals"}</span></button>)}
          </div>

          {!selectedChangeIndustry && <div className="context-change-overview-grid">{changeIndustrySummaries.map((summary) => <button type="button" key={summary.id} onClick={() => setChangeIndustryId(summary.id)}>
            <header><span>{summary.latest ? displayDate(summary.latest) : "No dated release"}</span><small>{summary.publishers} {summary.publishers === 1 ? "publisher" : "publishers"}</small></header>
            <h3>{summary.name}</h3>
            <div><strong>{summary.documents.length}</strong><span>recent {summary.documents.length === 1 ? "release" : "releases"}</span><strong>{summary.signals}</strong><span>{summary.signals === 1 ? "signal" : "signals"} represented</span></div>
            <p>{summary.documents[0]?.title || "No recent publication activity in the bounded sample."}</p>
            <footer>Open industry activity →</footer>
          </button>)}</div>}

          {selectedChangeIndustry && <div className="context-change-signal-groups">{selectedChangeGroups.map(({ collection, signals, documents }) => collection && <article key={collection.collection_ref}>
            <header><div><span>{collection.publisher}</span><h3>{collection.display_name}</h3></div><strong>{documents.length}<small>{documents.length === 1 ? "release" : "releases"}</small></strong></header>
            <p>{collection.description}</p>
            <div className="context-change-group-meta"><span>{readable(collection.cadence)}</span><span>{collection.freshness_status}</span><span>Latest {displayDate(documents[0].browse_date)}</span></div>
            <div className="context-change-potential-signals"><small>Potentially informs</small>{signals.map((signal) => <span key={signal.id}>{signal.name}</span>)}</div>
            <div className="context-change-group-documents">{documents.slice(0, 5).map((document) => <div key={document.document_id}><time>{displayDate(document.browse_date)}</time><div><strong>{document.title}</strong><span>{document.publisher} · {document.collection}</span></div><a href={document.source_url} target="_blank" rel="noreferrer">PDF</a></div>)}</div>
          </article>)}{!selectedChangeGroups.length && <div className="context-explore-empty">No recent releases from this industry's signal-linked collections appear in the bounded activity sample.</div>}</div>}
        </section>
      )}

      {entrance === "publishers" && (
        <section className="context-institution-workspace">
          <aside className="context-institution-rail">
            {contextMap.institution_groups.map((group) => <details key={group.name} open={group.name === selectedPublisher?.group}><summary>{group.name}<small>{group.publishers.length}</small></summary><div>{group.publishers.map((publisher) => <button type="button" className={publisher.id === selectedPublisher?.id ? "selected" : ""} key={publisher.id} onClick={() => { setPublisherId(publisher.id); setPublisherConnectionFilter("all"); }}><span>{publisher.name}</span><small>{publisher.collection_refs.length}</small></button>)}</div></details>)}
          </aside>
          <div className="context-institution-main">{selectedPublisher && <>
            <header>
              <span>{selectedPublisher.group}</span>
              <h2>{selectedPublisher.name}</h2>
              <p>{readable(selectedPublisher.authority_type)} · {selectedPublisher.collection_refs.length} publication collections · {selectedPublisher.initial_parse_document_count.toLocaleString()} selected PDFs</p>
              <div className="context-publisher-summary">
                <strong>{publisherCollections.filter((collection) => collection && mappedCollectionRefs.has(collection.collection_ref)).length}<small>signal-linked collections</small></strong>
                <strong>{publisherCollections.filter((collection) => collection && !mappedCollectionRefs.has(collection.collection_ref)).length}<small>without signal links</small></strong>
                <strong>{publisherReviewedIndustries.length}<small>{publisherReviewedIndustries.length > 3 ? "reviewed cross-sector links" : "reviewed industries"}</small></strong>
              </div>
              {publisherReviewedIndustries.length > 0 ? <div className="context-tag-row">{publisherReviewedIndustries.map((tag) => <span key={tag}>{tag}</span>)}</div> : <p className="context-unmapped-note">This institution is discoverable, but none of its collections has a reviewed industry-signal connection yet.</p>}
            </header>
            <div className="context-inline-filters" role="group" aria-label="Filter this publisher's collections">
              {(["all", "mapped", "unmapped"] as ConnectionFilter[]).map((filter) => <button type="button" key={filter} className={publisherConnectionFilter === filter ? "selected" : ""} onClick={() => setPublisherConnectionFilter(filter)}>{filter === "all" ? "All collections" : filter === "mapped" ? "Reviewed signal links" : "No signal link yet"}</button>)}
            </div>
            <div className="context-publisher-collections">{visiblePublisherCollections.map((collection) => collection && <article key={collection.collection_ref}>
              <div><span>{readable(collection.retention_policy)}</span><small>{collection.freshness_status}</small></div>
              <div className={`context-connection-badge ${mappedCollectionRefs.has(collection.collection_ref) ? "mapped" : "unmapped"}`}>{mappedCollectionRefs.has(collection.collection_ref) ? "Reviewed signal link" : "No signal link yet"}</div>
              <h3>{collection.display_name}</h3>
              <p>{collection.description}</p>
              {(reviewedIndustriesByCollection.get(collection.collection_ref) || []).length > 0 && <div className="context-card-tags">{(reviewedIndustriesByCollection.get(collection.collection_ref) || []).map((tag) => <span key={tag}>{tag}</span>)}</div>}
              {!mappedCollectionRefs.has(collection.collection_ref) && collection.industry_tags.length > 0 && <div className="context-card-tags candidate"><small>Candidate tags</small>{collection.industry_tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
              <footer><span>{collection.document_count.toLocaleString()} total · {collection.initial_parse_document_count.toLocaleString()} selected</span><div><button type="button" onClick={() => toggleTracked(collection.collection_ref)}>{trackedRefs.includes(collection.collection_ref) ? "Tracked" : "Track"}</button><Link href={`/library-preview?collection_ref=${encodeURIComponent(collection.collection_ref)}&view=initial`}>Open folder →</Link></div></footer>
            </article>)}</div>
          </>}</div>
        </section>
      )}

      {entrance === "explore" && (
        <section className="context-explore-view">
          <header>
            <div><span className="kicker">Complete source inventory</span><h2>Explore beyond the curated map.</h2><p>Industry connections express reviewed judgment. This catalog keeps every captured collection discoverable—including sources that have not yet earned a canonical connection.</p></div>
            <Link href="/library-preview">Open folder archive →</Link>
          </header>

          <div className="context-explore-stats">
            <span><strong>{contextMap.summary.total_collections}</strong> all collections</span>
            <span><strong>{mappedCollectionRefs.size}</strong> signal-linked collections</span>
            <span><strong>{contextMap.collections.filter((collection) => !mappedCollectionRefs.has(collection.collection_ref) && collection.scope_status === "monitored").length}</strong> monitored, unmapped</span>
            <span><strong>{contextMap.collections.filter((collection) => collection.scope_status === "parked").length}</strong> parked</span>
            <span><strong>{trackedRefs.length}</strong> tracked by you</span>
          </div>

          <div className="context-explore-controls">
            <label><span>Find a publisher or publication</span><input value={exploreQuery} onChange={(event) => setExploreQuery(event.target.value)} placeholder="Try SBP, procurement, health regulations…" /></label>
            <label><span>Connection</span><select value={exploreConnection} onChange={(event) => setExploreConnection(event.target.value as ConnectionFilter)}><option value="all">All connections</option><option value="mapped">Reviewed signal link</option><option value="unmapped">No signal link yet</option></select></label>
            <label><span>Monitoring scope</span><select value={exploreScope} onChange={(event) => setExploreScope(event.target.value)}><option value="monitored">Monitored</option><option value="parked">Parked</option><option value="all">All scope</option></select></label>
            <label><span>Retention</span><select value={exploreRetention} onChange={(event) => setExploreRetention(event.target.value)}><option value="all">All retention</option><option value="recent_series">Recent series</option><option value="governing_baseline">Governing baseline</option><option value="latest_edition">Latest edition</option><option value="active_register">Active register</option><option value="archive_only">Archive only</option></select></label>
          </div>

          <div className="context-explore-result-heading"><strong>{exploredCollections.length.toLocaleString()} collections</strong><span>Publisher → publication collection → source folder</span></div>
          <div className="context-explore-grid">{exploredCollections.map((collection) => {
            const mapped = mappedCollectionRefs.has(collection.collection_ref);
            const tracked = trackedRefs.includes(collection.collection_ref);
            return <article key={collection.collection_ref}>
              <header><span>{collection.publisher}</span><small>{readable(collection.retention_policy)}</small></header>
              <h3>{collection.display_name}</h3>
              <p>{collection.description}</p>
              <div className="context-explore-badges"><span className={mapped ? "mapped" : "unmapped"}>{mapped ? "Reviewed signal link" : "No signal link yet"}</span><span>{readable(collection.cadence)}</span><span>{collection.freshness_status}</span></div>
              {(reviewedIndustriesByCollection.get(collection.collection_ref) || []).length > 0 && <div className="context-card-tags">{(reviewedIndustriesByCollection.get(collection.collection_ref) || []).map((tag) => <span key={tag}>{tag}</span>)}</div>}
              {!mapped && collection.industry_tags.length > 0 && <div className="context-card-tags candidate"><small>Candidate tags</small>{collection.industry_tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
              <footer><span>{collection.document_count.toLocaleString()} PDFs · {collection.initial_parse_document_count.toLocaleString()} selected</span><div><button type="button" className={tracked ? "selected" : ""} onClick={() => toggleTracked(collection.collection_ref)}>{tracked ? "Tracked" : "Track source"}</button><Link href={`/library-preview?collection_ref=${encodeURIComponent(collection.collection_ref)}&view=initial`}>Browse →</Link></div></footer>
            </article>;
          })}</div>
          {!exploredCollections.length && <div className="context-explore-empty">No collections match these filters. The canonical map has not been changed.</div>}
        </section>
      )}
    </main>
  );
}
