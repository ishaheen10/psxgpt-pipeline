import registry from "../../data/industry-packs.json";
import Link from "next/link";
import IndustryPackLink from "./industry-pack-link";

export const dynamic = "force-static";

const STATE_LABELS: Record<string, string> = {
  parse_candidate: "Ready to parse",
  metadata_monitor: "Track metadata",
  blocked: "Access blocked",
  source_gap: "Source gap",
  partial_source_coverage: "Partial source coverage",
  route_probation: "Pakistan route under observation",
  on_demand: "On demand",
  unresolved: "Needs review",
};

const DIMENSION_LABELS: Record<string, string> = {
  inputs: "Inputs",
  operations: "Operations",
  output_demand: "Output & demand",
  prices_market: "Prices & market",
  policy_regulation: "Policy & rules",
};

const DIMENSION_DESCRIPTIONS: Record<string, string> = {
  inputs: "What changes the cost or availability of feedstock, energy, finance and imported materials?",
  operations: "What shows actual production, capacity, utilization, inventory or service activity between statements?",
  output_demand: "What reveals sales demand, imports, exports, consumption and end-market activity?",
  prices_market: "What moves selling prices, tariffs, benchmarks, spreads and market clearing conditions?",
  policy_regulation: "What changes the rules, taxes, licences, allocations, obligations or competitive structure?",
};

const STATUS_LABELS: Record<string, string> = {
  ready: "Strong starting pack",
  ready_after_hs_review: "Needs product-code review",
  ready_after_value_chain_tags: "Needs value-chain tags",
  ready_with_cppa_gap: "Strong, with one feed gap",
  partial: "Useful, still incomplete",
  partial_requires_subtype: "Needs company subtype",
  partial_requires_business_model: "Needs business-model split",
  partial_requires_subtype_and_jurisdiction: "Needs subtype and jurisdiction",
  partial_missing_operations_data: "Missing operating data",
  partial_with_port_core: "Useful port core, mode-specific gaps remain",
  blocked_by_product_mapping: "Needs product mapping",
  blocked_by_mode_mapping: "Needs transport-mode mapping",
};

const HEALTH_STAGE_LABELS: Record<string, string> = {
  boundary: "URL boundary",
  discovery: "Find URLs",
  access: "Access",
  download: "Downloads",
  metadata: "Date metadata",
  assets: "Page assets",
  parse: "Parsing",
  refresh: "Refresh evidence",
};

const HEALTH_STAGES = Object.keys(HEALTH_STAGE_LABELS);

type FeedGroup = "pulse" | "event" | "baseline";

const KIND_GROUP: Record<string, FeedGroup> = {
  recurring_statistic: "pulse",
  recurring_and_event: "pulse",
  structured_current_and_event: "pulse",
  regulatory_event: "event",
  publication_stream: "baseline",
};

const GROUP_ORDER: FeedGroup[] = ["pulse", "event", "baseline"];

const GROUP_LABELS: Record<FeedGroup, string> = {
  pulse: "Recurring pulse",
  event: "Event-driven change",
  baseline: "Standing baseline",
};

const GROUP_DESCRIPTIONS: Record<FeedGroup, string> = {
  pulse: "Arrives on a regular schedule. Read it to see whether the trend is turning.",
  event: "Arrives only when something happens — a rule, licence, price move or decision. Read it to catch the change itself.",
  baseline: "Reference material that rarely changes. Consult it to understand the structure the trend moves within.",
};

function groupFeeds(pack: (typeof registry.packs)[number]) {
  const groups: Record<FeedGroup, typeof pack.feeds> = { pulse: [], event: [], baseline: [] };
  pack.feeds.forEach((feed) => {
    groups[KIND_GROUP[feed.kind] || "baseline"].push(feed);
  });
  return groups;
}

const ARTIFACT_DESCRIPTIONS: Record<string, string> = {
  source_catalog: "Every discovered first-party document identity and its collection.",
  source_manifest: "The authoritative files actually held locally, with provenance.",
  metadata_audit: "Year basis, confidence, conflicts, and unresolved dates.",
  system_health: "Deterministic stage checks for every industry pack.",
  parse_queue: "The selected documents and first-page budget approved for parsing.",
  metadata_queue: "Documents held at metadata level until an event or question makes them relevant.",
  asset_manifest: "Which documents have page PDFs, thumbnails, and object-store assets.",
  parse_manifest: "Which pages are searchable, need OCR, or recorded extraction errors.",
};

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

function feedCount(pack: (typeof registry.packs)[number], state: string) {
  return pack.feeds.filter((feed) => feed.deployment_state === state).length;
}

function healthCount(stage: string, status: string) {
  const counts = registry.health_snapshot.stage_statuses[stage as keyof typeof registry.health_snapshot.stage_statuses] as Record<string, number>;
  return counts?.[status] || 0;
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) return "No recorded run";
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value));
}

function displayStageStatus(pack: (typeof registry.packs)[number], stage: string) {
  if ((stage === "assets" || stage === "parse") && pack.health.execution_status === "not_started") return "not_started";
  return pack.health.stages[stage as keyof typeof pack.health.stages].status;
}

export default function IndustriesPage() {
  return (
    <main className="industry-map">
      <header className="industry-map-header">
        <Link className="industry-map-brand" href="/">
          <span>L</span>
          <strong>Ledger</strong>
        </Link>
        <div>
          <span className="industry-map-kicker">Pakistan authoritative context map</span>
          <h1>Which publications explain an industry?</h1>
          <p>
            Start with the operating model. Then track only the official publications that measure a key driver,
            change a rule, or explain an event. Each publication below says who publishes it, when it arrives,
            what it tells us, why it matters, and how it connects to the industry.
          </p>
        </div>
      </header>

      <section className="industry-map-summary" aria-label="Coverage summary">
        <div><strong>{registry.summary.industry_packs}</strong><span>industry packs</span></div>
        <div><strong>{registry.summary.declared_source_selections}</strong><span>publication linkages</span></div>
        <div><strong>{formatNumber(registry.summary.unique_selected_documents)}</strong><span>selected documents</span></div>
        <div><strong>{formatNumber(registry.summary.initial_page_budget)}</strong><span>first-pass pages</span></div>
      </section>

      <section className="evidence-method" aria-labelledby="evidence-method-title">
        <header>
          <span className="industry-map-kicker">The selection rule</span>
          <h2 id="evidence-method-title">A publication earns its place by answering a driver question</h2>
          <p>
            We are not mirroring every government PDF. We first describe how an industry makes money and what can
            change that picture, then attach the smallest authoritative publication set that observes those changes.
          </p>
        </header>
        <div className="evidence-dimension-grid">
          {Object.entries(DIMENSION_LABELS).map(([dimension, label], index) => (
            <article className={`dimension-${dimension}`} key={dimension}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{label}</strong>
              <p>{DIMENSION_DESCRIPTIONS[dimension]}</p>
            </article>
          ))}
        </div>
        <p className="evidence-method-note">
          A single publication can serve several links. NFDC&apos;s monthly fertilizer review, for example, connects
          operations, demand, inventory, trade and prices; OGRA publications connect the gas input chain to cost and policy.
        </p>
      </section>

      <nav className="industry-chooser" aria-label="Choose an industry">
        <header>
          <span className="industry-map-kicker">23 industries</span>
          <h2>Choose an industry to read</h2>
          <p>Each card opens in place below — no other industry collapses or reflows around it.</p>
        </header>
        <div className="industry-chooser-grid">
          {registry.packs.map((pack, index) => {
            const groups = groupFeeds(pack);
            return (
              <IndustryPackLink className="chooser-card" packId={pack.id} key={pack.id}>
                <span className="chooser-index">{String(index + 1).padStart(2, "0")}</span>
                <h3>{pack.name}</h3>
                <p>{pack.economic_identity}</p>
                <div className="chooser-mix" aria-label="Publication mix">
                  {GROUP_ORDER.filter((group) => groups[group].length > 0).map((group) => (
                    <span className={`mix-${group}`} key={group}>{groups[group].length} {GROUP_LABELS[group].split(" ")[0].toLowerCase()}</span>
                  ))}
                </div>
              </IndustryPackLink>
            );
          })}
        </div>
        <a className="chooser-card chooser-card-system" href="#system-health">
          <h3>System health</h3>
          <p>Can this system actually find, refresh and parse the context behind every card above?</p>
        </a>
      </nav>

      <section className="industry-pack-list">
        {registry.packs.map((pack, index) => {
          const groups = groupFeeds(pack);
          return (
          <details className="industry-pack" id={pack.id} key={pack.id}>
            <summary className="industry-pack-summary">
              <span className="industry-pack-number">{String(index + 1).padStart(2, "0")}</span>
              <div className="industry-pack-summary-text">
                <h2>{pack.name}</h2>
                <p>{pack.economic_identity}</p>
              </div>
              <div className="industry-pack-summary-meta">
                <span className={`pack-status status-${pack.selection_status}`}>{STATUS_LABELS[pack.selection_status] || pack.selection_status}</span>
                <span className="chooser-index-note">{pack.feeds.length} tracked publications</span>
              </div>
            </summary>

            <div className="industry-pack-body" id={`${pack.id}-open`}>
              {pack.id === "fertilizer" && (
                <section className="industry-report-entry">
                  <div>
                    <span className="industry-map-kicker">Investor workspace</span>
                    <h3>Turn this publication map into a testable sector view</h3>
                    <p>Read the current view, compare companies, inspect thesis tests and selectively add evidence to context.</p>
                  </div>
                  <Link href="/industries/fertilizer/report">Open fertilizer report →</Link>
                </section>
              )}
              <section className="operating-model-block">
                <h3>How {pack.name.toLowerCase()} makes money, in plain terms</h3>
                <div className="operating-model" aria-label={`${pack.name} operating model`}>
                  <div><span>Inputs</span><p>{pack.inputs.join(" · ") || "Company-specific"}</p></div>
                  <b aria-hidden="true">→</b>
                  <div className="operating-core"><span>Industry engine</span><p>{pack.state_variables.join(" · ")}</p></div>
                  <b aria-hidden="true">→</b>
                  <div><span>Outputs</span><p>{pack.outputs.join(" · ") || "Company-specific"}</p></div>
                </div>
              </section>

              <section className="key-questions-block">
                <h3>Questions that change the view</h3>
                <ul>{pack.key_questions.map((question) => <li key={question}>{question}</li>)}</ul>
                {pack.known_gaps.length > 0 && <p className="known-gap"><b>Known gap:</b> {pack.known_gaps.join(" ")}</p>}
              </section>

              <section className="publication-map" aria-label={`${pack.name} tracked publications`}>
                <header>
                  <span className="industry-map-kicker">Why each publication earns its place</span>
                  {pack.evidence_thesis && <p>{pack.evidence_thesis}</p>}
                </header>

                {GROUP_ORDER.filter((group) => groups[group].length > 0).map((group) => (
                  <div className={`publication-group group-${group}`} key={group}>
                    <div className="publication-group-heading">
                      <h4>{GROUP_LABELS[group]}</h4>
                      <p>{GROUP_DESCRIPTIONS[group]}</p>
                    </div>
                    <div className="publication-narrative">
                      {groups[group].map((feed, feedIndex) => {
                        const dimensions = feed.relationship_dimensions.length ? feed.relationship_dimensions : ["supporting_context"];
                        const measures = feed.measures.length ? feed.measures : feed.targets;
                        return (
                          <article className="publication-card" key={`${feed.signal_id}-${feedIndex}`}>
                            <div className="publication-relations">
                              {dimensions.map((dimension) => (
                                <span className={`dimension-${dimension}`} key={dimension}>{DIMENSION_LABELS[dimension] || "Supporting context"}</span>
                              ))}
                              <span className={`feed-state state-${feed.deployment_state}`}>{STATE_LABELS[feed.deployment_state]}</span>
                            </div>
                            <h5>{feed.name}</h5>
                            <p className="publication-why">{feed.why}</p>
                            <p className="publication-signals">
                              <strong>Reveals: </strong>
                              {measures.length ? measures.join(" · ") : "Event or standing context identified by the bounded collection filter"}
                            </p>
                            <p className="publication-support">
                              {feed.publisher} · {feed.release_frequency} · typical lag {feed.expected_lag}
                            </p>
                            <details className="publication-technical">
                              <summary>Sources and parse treatment</summary>
                              <p><b>Collections:</b> {feed.collection_refs.join(", ") || "Not yet registered"}</p>
                              <p><b>Selection:</b> {formatNumber(feed.selected_documents)} documents · {formatNumber(feed.initial_page_budget)} first-pass pages · {formatNumber(feed.metadata_candidates)} metadata candidates</p>
                              <p><b>Method:</b> {feed.treatment.replaceAll("_", " ")} · acquisition {feed.acquisition_state} · implementation {feed.implementation_state}</p>
                              {feed.official_urls.length > 0 && (
                                <p className="publication-links"><b>Official pages:</b> {feed.official_urls.map((url, urlIndex) => <span key={url}>{urlIndex > 0 && " · "}<a href={url} rel="noreferrer" target="_blank">{new URL(url).hostname}</a></span>)}</p>
                              )}
                              {feed.need && <p><b>Next refinement:</b> {feed.need}</p>}
                              {feed.missing_collection_refs.length > 0 && <p><b>Missing collection:</b> {feed.missing_collection_refs.join(", ")}</p>}
                            </details>
                          </article>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </section>

              <details className="pack-health">
                <summary>
                  <span className="industry-map-kicker">Behind this pack</span>
                  <span>Collection and parsing status</span>
                </summary>
                <div className="pack-health-summary">
                  <span className={`pack-health-state health-${pack.health.collection_status}`}>
                    Collection health: {pack.health.collection_status}
                  </span>
                  <span>Catalogue coverage <b>{pack.health.catalogue_completion_pct}%</b></span>
                  <span>Execution <b>{pack.health.execution_status.replaceAll("_", " ")}</b></span>
                  <span>Last evidence <b>{formatTimestamp(pack.health.stages.refresh.last_recorded_run)} UTC</b></span>
                </div>
                <div className="pack-stage-line" aria-label={`${pack.name} system health`}>
                  {HEALTH_STAGES.map((stage) => {
                    const status = displayStageStatus(pack, stage);
                    return <span className={`stage-${status}`} key={stage}><i />{HEALTH_STAGE_LABELS[stage]}<b>{status.replaceAll("_", " ")}</b></span>;
                  })}
                </div>
                <div className="pack-health-metrics">
                  <span><b>{pack.health.stages.discovery.collections_with_run_evidence}/{pack.health.stages.discovery.required_collections}</b> collections discovered</span>
                  <span><b>{formatNumber(pack.health.stages.download.documents)}</b> downloaded holdings</span>
                  <span><b>{pack.health.stages.metadata.reliable_year_pct}%</b> reliable document years</span>
                  <span><b>{formatNumber(pack.health.stages.parse.searchable_pages)}</b> searchable pages recorded</span>
                </div>
                <div className="pack-health-metrics">
                  <span><b>{formatNumber(pack.selected_document_count)}</b> selected documents</span>
                  <span><b>{formatNumber(pack.initial_page_budget)}</b> first-pass pages</span>
                  <span><b>{formatNumber(pack.metadata_candidate_count)}</b> metadata candidates</span>
                  <span><b>{feedCount(pack, "parse_candidate")}</b> parse now · <b>{feedCount(pack, "metadata_monitor")}</b> metadata only · <b>{feedCount(pack, "blocked") + feedCount(pack, "source_gap") + feedCount(pack, "partial_source_coverage")}</b> blocked or partial</span>
                </div>
                {(pack.health.stages.discovery.missing_run_evidence.length > 0
                  || pack.health.stages.access.parked_or_pending.length > 0
                  || pack.health.stages.download.empty_collection_refs.length > 0) && (
                  <details className="health-gaps">
                    <summary>Show collection-health gaps</summary>
                    {pack.health.stages.discovery.missing_run_evidence.length > 0 && <p><b>No run evidence:</b> {pack.health.stages.discovery.missing_run_evidence.join(", ")}</p>}
                    {pack.health.stages.access.parked_or_pending.length > 0 && <p><b>Parked or pending:</b> {pack.health.stages.access.parked_or_pending.join(", ")}</p>}
                    {pack.health.stages.download.empty_collection_refs.length > 0 && <p><b>No downloaded holdings:</b> {pack.health.stages.download.empty_collection_refs.join(", ")}</p>}
                  </details>
                )}
              </details>
            </div>
          </details>
          );
        })}
      </section>

      <section className="macro-overlay">
        <span className="industry-map-kicker">Shared across industries</span>
        <h2>Macro overlay</h2>
        <p>These publications provide common fiscal, monetary, inflation, and economy-wide context without being forced into one industry.</p>
        <div>
          {registry.macro_overlay.map((item) => (
            <article key={item.collection_ref}>
              <strong>{item.name}</strong>
              <span>{item.publisher}</span>
              <p>{item.why}</p>
              <small>{item.cadence.replaceAll("_", " ")} · {item.selected_documents} selected documents · {item.initial_page_budget} first-pass pages</small>
            </article>
          ))}
        </div>
      </section>

      <details className="system-health" id="system-health">
        <summary className="system-health-summary">
          <div>
            <span className="industry-map-kicker">Behind the editorial content</span>
            <h2>Can this system find, refresh and parse the context?</h2>
          </div>
          <span className="health-generated">Evidence generated {formatTimestamp(registry.health_snapshot.generated_at)} UTC</span>
        </summary>

        <div className="system-health-body">

        <div className="health-stage-grid">
          {HEALTH_STAGES.map((stage) => {
            const notStarted = stage === "assets" || stage === "parse";
            return (
              <article key={stage}>
                <span>{HEALTH_STAGE_LABELS[stage]}</span>
                {notStarted ? (
                  <><strong>{registry.health_snapshot.direct_pack_count}</strong><small>selected-queue runs not started</small></>
                ) : (
                  <>
                    <strong>{healthCount(stage, "green")}/{registry.health_snapshot.direct_pack_count}</strong>
                    <small>{healthCount(stage, "amber")} partial · {healthCount(stage, "red")} blocked or weak</small>
                  </>
                )}
              </article>
            );
          })}
        </div>

        <div className="health-explanation">
          <p><b>Collection health</b> measures URL boundaries, discovery, access, holdings, dates and refresh evidence.</p>
          <p><b>Execution health</b> begins when the bounded 763-document queue is converted to page assets and searchable text.</p>
          <p><b>Scheduler health is unknown.</b> We retain downloader runs, but not proof that a scheduled job fired on time.</p>
        </div>
        <p className="health-denominator-note">{registry.health_snapshot.denominator_note}</p>

        <section className="priority-gap-list">
          <header>
            <div>
              <span className="industry-map-kicker">Highest-leverage gaps</span>
              <h3>Fix one feed, improve several industries</h3>
            </div>
            <small>Ranked by number of linked industry packs</small>
          </header>
          <div>
            {registry.priority_gaps.slice(0, 6).map((gap) => (
              <article key={gap.signal_id}>
                <strong>{gap.name}</strong>
                <span>{gap.industry_count} {gap.industry_count === 1 ? "industry" : "industries"}</span>
                <p>{gap.industries.join(" · ")}</p>
                <small>{gap.deployment_states.map((state) => STATE_LABELS[state] || state).join(" · ")}</small>
              </article>
            ))}
          </div>
        </section>

        <details className="artifact-register">
          <summary>Artifacts that prove system health</summary>
          <div>
            {Object.entries(registry.artifacts)
              .filter(([key]) => ARTIFACT_DESCRIPTIONS[key])
              .map(([key, path]) => (
                <article key={key}>
                  <strong>{key.replaceAll("_", " ")}</strong>
                  <p>{ARTIFACT_DESCRIPTIONS[key]}</p>
                  <code>{path}</code>
                </article>
              ))}
          </div>
        </details>
        </div>
      </details>

      <footer className="industry-map-footer">
        Planning snapshot as of {registry.as_of}. Source registry generated {new Date(registry.source_generated_at).toLocaleDateString("en-GB")}.
      </footer>
    </main>
  );
}
