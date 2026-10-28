import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = resolve(
  process.env.LEDGER_INDUSTRY_PACK_SUMMARY
    || resolve(appRoot, "../../../ledger-pakistan/artifacts/13_industry_pack_plans/summary.json"),
);
const healthPath = resolve(
  process.env.LEDGER_INDUSTRY_PACK_HEALTH
    || resolve(appRoot, "../../../ledger-pakistan/artifacts/11_industry_pack_health/health.json"),
);
const signalCatalogPath = resolve(
  process.env.LEDGER_INDUSTRY_SIGNAL_CATALOG
    || resolve(appRoot, "../../../ledger-pakistan/config/industry_signal_catalog.json"),
);
const corpusLibraryPath = resolve(
  process.env.LEDGER_CORPUS_LIBRARY
    || resolve(appRoot, "../../../ledger-pakistan/config/corpus_library.json"),
);
const outputPath = resolve(appRoot, "data/industry-packs.json");

function stableSignalId(feed) {
  if (feed.signal_id) return feed.signal_id;
  return String(feed.name || "unnamed-feed")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function deploymentState(feed, health) {
  if (feed.implementation_state === "blocked" || ["blocked", "parked"].includes(feed.acquisition_state)) return "blocked";
  if ((feed.missing_collection_refs || []).length) return "source_gap";
  if (feed.treatment === "on_demand") return "on_demand";
  if (Number(feed.selected_documents || 0) > 0) {
    const emptyRefs = new Set(health?.stages?.download?.empty_collection_refs || []);
    if ((feed.collection_refs || []).some((collectionRef) => emptyRefs.has(collectionRef))) return "partial_source_coverage";
    if (feed.acquisition_state === "relay_operational_probation") return "route_probation";
    return "parse_candidate";
  }
  if (Number(feed.metadata_candidates || 0) > 0 || feed.treatment === "metadata_only") return "metadata_monitor";
  return "unresolved";
}

function collectionHealth(stages) {
  const statuses = ["boundary", "discovery", "access", "download", "metadata", "refresh"]
    .map((stage) => stages?.[stage]?.status)
    .filter(Boolean);
  if (statuses.includes("red")) return "red";
  if (statuses.includes("amber") || statuses.includes("unknown")) return "amber";
  return statuses.length ? "green" : "unknown";
}

function executionHealth(stages) {
  const parseCoverage = Number(stages?.parse?.coverage_pct || 0);
  const assetCoverage = Number(stages?.assets?.coverage_pct || 0);
  if (parseCoverage >= 95 && assetCoverage >= 95 && Number(stages?.parse?.extraction_errors || 0) === 0) return "green";
  if (parseCoverage > 0 || assetCoverage > 0) return "amber";
  return "not_started";
}

const source = JSON.parse(await readFile(sourcePath, "utf8"));
const healthSource = JSON.parse(await readFile(healthPath, "utf8"));
const signalCatalog = JSON.parse(await readFile(signalCatalogPath, "utf8"));
const corpusLibrary = JSON.parse(await readFile(corpusLibraryPath, "utf8"));
const signalById = new Map(Object.entries(signalCatalog.signals || {}));
const signalDimensionsByIndustry = new Map(
  (signalCatalog.industries || []).map((industry) => {
    const dimensionsBySignal = new Map();
    for (const [dimension, signalIds] of Object.entries(industry.dimensions || {})) {
      for (const signalId of signalIds) {
        const dimensions = dimensionsBySignal.get(signalId) || [];
        dimensions.push(dimension);
        dimensionsBySignal.set(signalId, dimensions);
      }
    }
    return [industry.id, { why: industry.why || "", dimensionsBySignal }];
  }),
);
const collectionById = new Map(
  [...(corpusLibrary.accepted_collections || []), ...(corpusLibrary.candidate_collections || [])]
    .map((collection) => [collection.id, collection]),
);

function matchingSignal(feed, industrySignalMap) {
  if (feed.signal_id && signalById.has(feed.signal_id)) return [feed.signal_id, signalById.get(feed.signal_id)];
  const feedRefs = new Set(feed.collection_refs || []);
  const candidates = [...(industrySignalMap?.dimensionsBySignal?.keys() || [])]
    .map((signalId) => {
      const signal = signalById.get(signalId);
      const overlap = (signal?.collection_refs || []).filter((ref) => feedRefs.has(ref)).length;
      return { signalId, signal, overlap };
    })
    .filter((candidate) => candidate.signal && candidate.overlap > 0)
    .sort((left, right) => right.overlap - left.overlap);
  return candidates.length ? [candidates[0].signalId, candidates[0].signal] : [null, null];
}
const healthByPack = new Map(healthSource.packs.map((pack) => [pack.industry_id, pack]));
const packs = source.packs
  .filter((pack) => pack.selection_status !== "inherit_only")
  .map((pack) => {
    const health = healthByPack.get(pack.id);
    const industrySignalMap = signalDimensionsByIndustry.get(pack.id);
    return ({
    id: pack.id,
    name: pack.name,
    selection_status: pack.selection_status,
    economic_identity: pack.economic_identity,
    inputs: pack.inputs || [],
    outputs: pack.outputs || [],
    state_variables: pack.state_variables || [],
    key_questions: pack.key_questions || [],
    scope_note: pack.scope_note || "",
    known_gaps: pack.known_gaps || [],
    explicit_exclusions: pack.explicit_exclusions || [],
    model_evidence: pack.model_evidence || [],
    selected_document_count: Number(pack.selected_document_count || 0),
    selected_full_pages: Number(pack.selected_full_pages || 0),
    initial_page_budget: Number(pack.initial_page_budget || 0),
    metadata_candidate_count: Number(pack.metadata_candidate_count || 0),
    health: health ? {
      catalogue_band: health.catalogue_band,
      catalogue_completion_pct: health.catalogue_completion_pct,
      collection_status: collectionHealth(health.stages),
      execution_status: executionHealth(health.stages),
      stages: health.stages,
    } : null,
    evidence_thesis: industrySignalMap?.why || "",
    feeds: (pack.selection_detail || []).map((feed) => {
      const [matchedSignalId, signal] = matchingSignal(feed, industrySignalMap);
      const collections = (feed.collection_refs || []).map((ref) => collectionById.get(ref)).filter(Boolean);
      return {
        signal_id: stableSignalId(feed),
        canonical_signal_id: matchedSignalId,
        name: feed.name,
        publisher: signal?.publisher || [...new Set(collections.map((collection) => collection.publisher))].join(" · ") || "Publisher mapping pending",
        relationship_dimensions: matchedSignalId ? industrySignalMap?.dimensionsBySignal?.get(matchedSignalId) || [] : [],
        kind: signal?.kind || "publication_stream",
        release_frequency: signal?.release_frequency || [...new Set(collections.map((collection) => collection.cadence).filter(Boolean))].join(" · ") || "Irregular / event-driven",
        expected_lag: signal?.expected_lag || "Not yet specified",
        measures: signal?.measures || feed.targets || [],
        official_urls: signal?.official_urls || collections.flatMap((collection) => collection.landing_urls || []),
        discovery_status: signal?.discovery_status || collections[0]?.status || "mapped",
        need: signal?.need || "",
        treatment: feed.treatment,
        why: feed.why,
        targets: feed.targets || [],
        collection_refs: feed.collection_refs || [],
        missing_collection_refs: feed.missing_collection_refs || [],
        implementation_state: feed.implementation_state,
        acquisition_state: feed.acquisition_state,
        deployment_state: deploymentState(feed, health),
        selected_documents: Number(feed.selected_documents || 0),
        selected_full_pages: Number(feed.selected_full_pages || 0),
        initial_page_budget: Number(feed.initial_page_budget || 0),
        metadata_candidates: Number(feed.metadata_candidates || 0),
        filter_status: feed.filter_status,
      };
    }),
  });
  });

const signalGroups = new Map();
for (const pack of packs) {
  for (const feed of pack.feeds) {
    const current = signalGroups.get(feed.signal_id) || {
      signal_id: feed.signal_id,
      name: feed.name,
      deployment_states: new Set(),
      industries: new Set(),
    };
    current.deployment_states.add(feed.deployment_state);
    current.industries.add(pack.name);
    signalGroups.set(feed.signal_id, current);
  }
}

const crossIndustrySignals = [...signalGroups.values()]
  .map((signal) => ({
    signal_id: signal.signal_id,
    name: signal.name,
    deployment_states: [...signal.deployment_states].sort(),
    industries: [...signal.industries].sort(),
    industry_count: signal.industries.size,
  }))
  .sort((left, right) => right.industry_count - left.industry_count || left.name.localeCompare(right.name));

const priorityGaps = crossIndustrySignals
  .filter((signal) => signal.deployment_states.some((state) => ["blocked", "source_gap", "partial_source_coverage"].includes(state)))
  .sort((left, right) => right.industry_count - left.industry_count || left.name.localeCompare(right.name));

const registry = {
  schema_version: "ledger-industry-feed-registry.v2",
  generated_at: new Date().toISOString(),
  source_schema_version: source.schema_version,
  source_generated_at: source.generated_at,
  as_of: source.as_of,
  recent_window_start: source.recent_window_start,
  summary: {
    industry_packs: packs.length,
    declared_source_selections: source.summary.declared_source_selections,
    unique_selected_documents: source.summary.unique_selected_documents,
    selected_full_pages: source.summary.selected_full_pages,
    initial_page_budget: source.summary.initial_page_budget,
    metadata_candidate_documents: source.summary.metadata_candidate_documents,
    missing_collection_refs: source.summary.missing_collection_refs || [],
    selection_statuses: source.summary.selection_statuses,
    treatments: source.summary.treatments,
  },
  health_snapshot: {
    generated_at: healthSource.generated_at,
    direct_pack_count: healthSource.summary.direct_pack_count,
    stage_statuses: healthSource.summary.stage_statuses,
    status_definitions: healthSource.status_definitions,
    important_limits: healthSource.important_limits,
    denominator_note: "Asset and parse coverage in the current health artifact uses every downloaded document linked to a pack. The selective 763-document plan is the intended execution denominator and must replace that broad denominator after the first bounded parse run.",
    scheduler_telemetry: "unknown",
  },
  artifacts: {
    planning_summary: "artifacts/13_industry_pack_plans/summary.json",
    parse_queue: "artifacts/13_industry_pack_plans/parse_queue.jsonl",
    metadata_queue: "artifacts/13_industry_pack_plans/metadata_queue.jsonl",
    system_health: "artifacts/11_industry_pack_health/health.json",
    health_report: "artifacts/11_industry_pack_health/report.md",
    source_catalog: "artifacts/01_authoritative_download/catalog.json",
    source_manifest: "artifacts/01_authoritative_download/manifest.json",
    metadata_audit: "artifacts/01_authoritative_metadata/last_applied_audit.json",
    asset_manifest: "artifacts/02_authoritative_assets/manifest.json",
    parse_manifest: "artifacts/03_authoritative_parse/manifest.json",
  },
  cross_industry_signals: crossIndustrySignals,
  priority_gaps: priorityGaps,
  packs,
  macro_overlay: (source.macro_overlay || []).map((item) => {
    const collection = collectionById.get(item.collection_ref);
    return {
      ...item,
      name: collection?.collection || item.collection_ref,
      publisher: collection?.publisher || "Publisher mapping pending",
      cadence: collection?.cadence || "Irregular",
      official_urls: collection?.landing_urls || [],
    };
  }),
};

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(registry, null, 2)}\n`);
console.log(`Wrote ${packs.length} industry packs to ${outputPath}`);
