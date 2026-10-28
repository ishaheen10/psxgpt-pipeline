import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ledgerRoot = resolve(process.env.LEDGER_ROOT || resolve(appRoot, "../../../ledger-pakistan"));
const packsPath = resolve(appRoot, "data/industry-packs.json");
const collectionsPath = resolve(appRoot, "data/library-preview/collections.json");
const documentsPath = resolve(appRoot, "data/library-preview/documents.jsonl");
const sourcesPath = resolve(ledgerRoot, "config/authoritative_sources.json");
const outputPath = resolve(appRoot, "data/context-map.json");

const packs = JSON.parse(await readFile(packsPath, "utf8"));
const collectionsSource = JSON.parse(await readFile(collectionsPath, "utf8"));
const documents = (await readFile(documentsPath, "utf8"))
  .split("\n")
  .filter(Boolean)
  .map((line) => JSON.parse(line));
const sources = JSON.parse(await readFile(sourcesPath, "utf8")).sources;

function stringArray(value) {
  if (Array.isArray(value)) return value.map(String);
  try {
    const parsed = JSON.parse(value || "[]");
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

const collectionByRef = new Map(collectionsSource.map((collection) => [collection.collection_ref, {
  collection_ref: collection.collection_ref,
  publisher_id: collection.publisher_id,
  publisher: collection.publisher,
  display_name: collection.display_name,
  description: collection.description,
  why_tracked: collection.why_tracked,
  cadence: collection.publication_cadence || collection.cadence,
  freshness_status: collection.freshness_status,
  freshness_note: collection.freshness_note,
  latest_browse_date: collection.latest_browse_date,
  latest_browse_year: collection.latest_browse_year,
  document_count: collection.document_count,
  initial_parse_document_count: collection.initial_parse_document_count,
  archive_document_count: collection.archive_document_count,
  retention_policy: collection.retention_policy,
  scope_status: collection.scope_status,
  industry_tags: stringArray(collection.industry_tags),
  signal_tags: stringArray(collection.signal_tags),
}]));

const connectedRefs = new Set();
const signalIds = new Set();
let connectionCount = 0;
const industries = packs.packs.map((pack) => {
  const signals = new Map();
  for (const feed of pack.feeds) {
    const id = feed.canonical_signal_id || feed.signal_id;
    signalIds.add(id);
    const current = signals.get(id) || {
      id,
      name: feed.name,
      why: feed.why,
      kind: feed.kind,
      release_frequency: feed.release_frequency,
      expected_lag: feed.expected_lag,
      measures: feed.measures || feed.targets || [],
      driver_ids: [],
      collection_refs: [],
      deployment_state: feed.deployment_state,
      implementation_state: feed.implementation_state,
    };
    current.driver_ids = [...new Set([...current.driver_ids, ...(feed.relationship_dimensions || [])])];
    current.collection_refs = [...new Set([...current.collection_refs, ...(feed.collection_refs || [])])];
    signals.set(id, current);
    for (const ref of feed.collection_refs || []) connectedRefs.add(ref);
  }
  connectionCount += [...signals.values()].reduce((sum, signal) => sum + signal.collection_refs.length, 0);
  return {
    id: pack.id,
    name: pack.name,
    economic_identity: pack.economic_identity,
    selection_status: pack.selection_status,
    evidence_thesis: pack.evidence_thesis,
    inputs: pack.inputs,
    outputs: pack.outputs,
    state_variables: pack.state_variables,
    key_questions: pack.key_questions,
    known_gaps: pack.known_gaps,
    signals: [...signals.values()],
  };
});

const sourceById = new Map(sources.map((source) => [source.id, source]));

function institutionGroup(publisherId, authorityType, scopeStatus) {
  if (scopeStatus === "parked") return "Discovery archive";
  if (publisherId.includes("ajk_") || publisherId.includes("gb_") || authorityType.startsWith("provincial_") || authorityType.startsWith("subnational_")) return "Provincial institutions";
  if (authorityType.includes("statistics") || authorityType.includes("research")) return "Statistics & recurring signals";
  if (authorityType.includes("regulator") || ["central_bank", "tax_authority", "exchange", "higher_education_regulator", "trade_remedies_regulator", "therapeutic_goods_regulator"].includes(authorityType)) return "Regulators & market authorities";
  if (["port_authority", "power_market_operator", "maritime_safety_administration", "transport_safety_and_enforcement_agency", "trade_authority", "trade_body"].includes(authorityType)) return "Infrastructure & system operators";
  return "Law, policy & ministries";
}

const publisherGroups = new Map();
for (const collection of collectionByRef.values()) {
  const source = sourceById.get(collection.publisher_id) || {};
  const group = institutionGroup(collection.publisher_id, source.authority_type || "unknown", collection.scope_status);
  const groupPublishers = publisherGroups.get(group) || new Map();
  const publisher = groupPublishers.get(collection.publisher_id) || {
    id: collection.publisher_id,
    name: collection.publisher,
    authority_type: source.authority_type || "unknown",
    collection_refs: [],
    industry_tags: [],
    initial_parse_document_count: 0,
    archive_document_count: 0,
  };
  publisher.collection_refs.push(collection.collection_ref);
  publisher.industry_tags = [...new Set([...publisher.industry_tags, ...collection.industry_tags])].sort();
  publisher.initial_parse_document_count += Number(collection.initial_parse_document_count || 0);
  publisher.archive_document_count += Number(collection.archive_document_count || 0);
  groupPublishers.set(collection.publisher_id, publisher);
  publisherGroups.set(group, groupPublishers);
}

const groupOrder = [
  "Law, policy & ministries",
  "Regulators & market authorities",
  "Statistics & recurring signals",
  "Infrastructure & system operators",
  "Provincial institutions",
  "Discovery archive",
];

const institution_groups = groupOrder.map((name) => ({
  name,
  publishers: [...(publisherGroups.get(name)?.values() || [])].sort((left, right) => left.name.localeCompare(right.name)),
})).filter((group) => group.publishers.length);

const recentDocuments = [];
const perCollection = new Map();
const generatedDate = new Date().toISOString().slice(0, 10);
const futureDatedDocumentCount = documents.filter(
  (document) => document.browse_date && String(document.browse_date) > generatedDate,
).length;
for (const document of documents
  .filter((document) => document.initial_parse_selected && document.browse_date && String(document.browse_date) <= generatedDate)
  .sort((left, right) => String(right.browse_date).localeCompare(String(left.browse_date)))) {
  const count = perCollection.get(document.collection_ref) || 0;
  if (count >= 3) continue;
  const collection = collectionByRef.get(document.collection_ref);
  if (!collection) continue;
  perCollection.set(document.collection_ref, count + 1);
  recentDocuments.push({
    document_id: document.document_id,
    title: document.title,
    browse_date: document.browse_date,
    browse_year: document.browse_year,
    collection_ref: document.collection_ref,
    collection: collection.display_name,
    publisher: collection.publisher,
    source_url: document.source_url,
    retention_lane: document.retention_lane,
    page_count: document.page_count,
  });
  if (recentDocuments.length >= 90) break;
}

const contextMap = {
  schema_version: "ledger-context-map.v1",
  generated_at: new Date().toISOString(),
  summary: {
    industries: industries.length,
    signals: signalIds.size,
    connected_collections: connectedRefs.size,
    total_collections: collectionsSource.length,
    publishers: institution_groups.reduce((sum, group) => sum + group.publishers.length, 0),
    industry_signal_collection_connections: connectionCount,
    future_dated_documents_excluded: futureDatedDocumentCount,
  },
  drivers: [
    { id: "inputs", label: "Inputs", question: "What changes the cost or availability of feedstock, energy, finance and imported materials?" },
    { id: "operations", label: "Operations", question: "What reveals production, capacity, utilization, inventory or service activity?" },
    { id: "output_demand", label: "Output & demand", question: "What reveals demand, sales, imports, exports and end-market activity?" },
    { id: "prices_market", label: "Prices & market", question: "What moves tariffs, prices, benchmarks, spreads and market conditions?" },
    { id: "policy_regulation", label: "Policy & rules", question: "What changes rules, taxes, licences, obligations or competitive structure?" },
  ],
  industries,
  collections: [...collectionByRef.values()],
  institution_groups,
  recent_documents: recentDocuments,
};

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(contextMap, null, 2)}\n`);
console.log(`Wrote ${industries.length} industries, ${connectedRefs.size} connected collections and ${recentDocuments.length} recent documents to ${outputPath}`);
