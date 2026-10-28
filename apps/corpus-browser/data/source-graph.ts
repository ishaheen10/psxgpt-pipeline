import registry from "./context-map.json" with { type: "json" };

export type GraphNodeType = "industry" | "driver" | "signal" | "collection" | "publisher";
export type GraphTrust = "catalog_fact" | "analyst_mapping";

export type SourceGraphNode = {
  id: string;
  type: GraphNodeType;
  label: string;
  description: string;
  anchorId?: string;
  parentId?: string;
  childIds: string[];
  metadata: Record<string, string | number | string[]>;
};

export type SourceGraphEdge = {
  id: string;
  source: string;
  target: string;
  relation: "frames" | "explains" | "observed_by" | "publishes";
  trust: GraphTrust;
  reason: string;
};

export type IndustryGraph = {
  industry: { id: string; name: string; economic_identity: string; key_questions: string[]; known_gaps: string[] };
  nodes: SourceGraphNode[];
  edges: SourceGraphEdge[];
};

type RegistryIndustry = (typeof registry.industries)[number];
type RegistrySignal = RegistryIndustry["signals"][number];
type RegistryCollection = (typeof registry.collections)[number];

const collectionByRef = new Map(registry.collections.map((collection) => [collection.collection_ref, collection]));
const driverById = new Map(registry.drivers.map((driver) => [driver.id, driver]));

function nodeId(type: GraphNodeType, id: string) {
  return `${type}:${id}`;
}

function signalDrivers(signal: RegistrySignal) {
  return signal.driver_ids.length ? signal.driver_ids : ["supporting_context"];
}

function collectionDescription(collection: RegistryCollection) {
  return collection.why_tracked || collection.description || "Authoritative publication family retained in the source catalogue.";
}

export function buildIndustryGraph(industryId: string): IndustryGraph {
  const industry = registry.industries.find((candidate) => candidate.id === industryId) || registry.industries[0];
  const nodes = new Map<string, SourceGraphNode>();
  const edges = new Map<string, SourceGraphEdge>();
  const industryNodeId = nodeId("industry", industry.id);

  nodes.set(industryNodeId, {
    id: industryNodeId,
    type: "industry",
    label: industry.name,
    description: industry.economic_identity,
    childIds: [],
    metadata: {
      status: industry.selection_status,
      questions: industry.key_questions,
      known_gaps: industry.known_gaps,
    },
  });

  const usedDriverIds = [...new Set(industry.signals.flatMap(signalDrivers))];
  usedDriverIds.forEach((driverId) => {
    const driver = driverById.get(driverId);
    const id = nodeId("driver", driverId);
    nodes.set(id, {
      id,
      type: "driver",
      label: driver?.label || "Supporting context",
      description: driver?.question || "A retained source relationship without a narrower explicit driver label.",
      parentId: industryNodeId,
      childIds: [],
      metadata: {},
    });
    nodes.get(industryNodeId)?.childIds.push(id);
    edges.set(`edge:${industryNodeId}:${id}`, {
      id: `edge:${industryNodeId}:${id}`,
      source: industryNodeId,
      target: id,
      relation: "frames",
      trust: "analyst_mapping",
      reason: driver?.question || "Retained as supporting context in the industry operating model.",
    });
  });

  industry.signals.forEach((signal) => {
    const id = nodeId("signal", signal.id);
    const primaryDriverId = nodeId("driver", signalDrivers(signal)[0]);
    nodes.set(id, {
      id,
      type: "signal",
      label: signal.name,
      description: signal.why,
      anchorId: primaryDriverId,
      childIds: [],
      metadata: {
        cadence: signal.release_frequency,
        expected_lag: signal.expected_lag,
        measures: signal.measures,
        deployment_state: signal.deployment_state,
      },
    });

    signalDrivers(signal).forEach((driverId) => {
      const driverNodeId = nodeId("driver", driverId);
      nodes.get(driverNodeId)?.childIds.push(id);
      const edgeId = `edge:${driverNodeId}:${id}`;
      edges.set(edgeId, {
        id: edgeId,
        source: driverNodeId,
        target: id,
        relation: "explains",
        trust: "analyst_mapping",
        reason: signal.why,
      });
    });

    signal.collection_refs.forEach((collectionRef) => {
      const collection = collectionByRef.get(collectionRef);
      if (!collection) return;
      const collectionNodeId = nodeId("collection", collectionRef);
      const collectionNode = nodes.get(collectionNodeId);
      if (!collectionNode) {
        nodes.set(collectionNodeId, {
          id: collectionNodeId,
          type: "collection",
          label: collection.display_name,
          description: collectionDescription(collection),
          anchorId: id,
          childIds: [],
          metadata: {
            collection_ref: collection.collection_ref,
            publisher: collection.publisher,
            cadence: collection.cadence,
            freshness: collection.freshness_status,
            latest_year: collection.latest_browse_year || "Unknown",
            documents: collection.document_count,
            retention: collection.retention_policy,
            scope_status: collection.scope_status,
          },
        });
      }
      if (!nodes.get(id)?.childIds.includes(collectionNodeId)) nodes.get(id)?.childIds.push(collectionNodeId);
      const mappingEdgeId = `edge:${id}:${collectionNodeId}`;
      edges.set(mappingEdgeId, {
        id: mappingEdgeId,
        source: id,
        target: collectionNodeId,
        relation: "observed_by",
        trust: "analyst_mapping",
        reason: `${signal.why} This publication family was selected in the industry pack to supply that evidence.`,
      });

      const publisherNodeId = nodeId("publisher", collection.publisher_id);
      if (!nodes.has(publisherNodeId)) {
        nodes.set(publisherNodeId, {
          id: publisherNodeId,
          type: "publisher",
          label: collection.publisher,
          description: `Authoritative publisher of ${collection.display_name}.`,
          anchorId: collectionNodeId,
          childIds: [],
          metadata: { publisher_id: collection.publisher_id },
        });
      }
      if (!nodes.get(collectionNodeId)?.childIds.includes(publisherNodeId)) nodes.get(collectionNodeId)?.childIds.push(publisherNodeId);
      const factEdgeId = `edge:${publisherNodeId}:${collectionNodeId}`;
      edges.set(factEdgeId, {
        id: factEdgeId,
        source: publisherNodeId,
        target: collectionNodeId,
        relation: "publishes",
        trust: "catalog_fact",
        reason: `${collection.publisher} is the catalogued publisher for ${collection.display_name}.`,
      });
    });
  });

  return {
    industry: {
      id: industry.id,
      name: industry.name,
      economic_identity: industry.economic_identity,
      key_questions: industry.key_questions,
      known_gaps: industry.known_gaps,
    },
    nodes: [...nodes.values()],
    edges: [...edges.values()],
  };
}

export const sourceGraphIndustries = registry.industries.map((industry) => ({
  id: industry.id,
  name: industry.name,
  signalCount: industry.signals.length,
  collectionCount: new Set(industry.signals.flatMap((signal) => signal.collection_refs)).size,
}));
