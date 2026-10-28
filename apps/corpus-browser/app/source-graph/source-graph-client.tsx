"use client";

import cytoscape, { type Core, type ElementDefinition, type EventObject } from "cytoscape";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  buildIndustryGraph,
  sourceGraphIndustries,
  type GraphNodeType,
  type SourceGraphNode,
} from "../../data/source-graph";
import fertilizerReport from "../../data/fertilizer-report.generated.json";

const typeLabels: Record<GraphNodeType, string> = {
  industry: "Industry",
  driver: "Driver",
  signal: "Publication purpose",
  collection: "Publication family",
  publisher: "Publisher",
};

const typeOrder: GraphNodeType[] = ["industry", "driver", "signal", "collection", "publisher"];

function shortLabel(label: string, max = 34) {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

function visibleGraph(
  graph: ReturnType<typeof buildIndustryGraph>,
  expandedSignals: Set<string>,
  expandedCollections: Set<string>,
  enabledTypes: Set<GraphNodeType>,
) {
  const nodes = graph.nodes.filter((node) => {
    if (node.type === "industry") return true;
    if (!enabledTypes.has(node.type)) return false;
    if (node.type === "collection") return Boolean(node.anchorId && expandedSignals.has(node.anchorId));
    if (node.type === "publisher") return Boolean(node.anchorId && expandedCollections.has(node.anchorId));
    return true;
  });
  const visibleIds = new Set(nodes.map((node) => node.id));
  return {
    nodes,
    edges: graph.edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
  };
}

function radialPositions(nodes: SourceGraphNode[]) {
  const positions = new Map<string, { x: number; y: number }>();
  const byType = new Map(typeOrder.map((type) => [type, nodes.filter((node) => node.type === type)]));
  const radii: Record<GraphNodeType, number> = { industry: 0, driver: 185, signal: 350, collection: 535, publisher: 700 };
  typeOrder.forEach((type) => {
    const group = byType.get(type) || [];
    if (type === "industry") {
      group.forEach((node) => positions.set(node.id, { x: 0, y: 0 }));
      return;
    }
    group.forEach((node, index) => {
      const angle = -Math.PI / 2 + (Math.PI * 2 * index) / Math.max(group.length, 1);
      positions.set(node.id, { x: Math.cos(angle) * radii[type], y: Math.sin(angle) * radii[type] });
    });
  });
  return positions;
}

function nodeBreadcrumb(node: SourceGraphNode, graph: ReturnType<typeof buildIndustryGraph>) {
  const byId = new Map(graph.nodes.map((candidate) => [candidate.id, candidate]));
  const labels = [node.label];
  let current = node;
  const visited = new Set<string>();
  while ((current.parentId || current.anchorId) && !visited.has(current.id)) {
    visited.add(current.id);
    const parent = byId.get(current.parentId || current.anchorId || "");
    if (!parent) break;
    labels.unshift(parent.label);
    current = parent;
  }
  if (labels[0] !== graph.industry.name) labels.unshift(graph.industry.name);
  return labels;
}

export default function SourceGraphClient() {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [industryId, setIndustryId] = useState("fertilizer");
  const [expandedSignals, setExpandedSignals] = useState<Set<string>>(new Set());
  const [expandedCollections, setExpandedCollections] = useState<Set<string>>(new Set());
  const [enabledTypes, setEnabledTypes] = useState<Set<GraphNodeType>>(new Set(typeOrder));
  const [selectedNodeId, setSelectedNodeId] = useState<string>("industry:fertilizer");
  const [selectedEdgeId, setSelectedEdgeId] = useState<string>("");
  const [contextIds, setContextIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [toast, setToast] = useState("");

  const graph = useMemo(() => buildIndustryGraph(industryId), [industryId]);
  const visible = useMemo(
    () => visibleGraph(graph, expandedSignals, expandedCollections, enabledTypes),
    [enabledTypes, expandedCollections, expandedSignals, graph],
  );
  const nodeById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  const edgeById = useMemo(() => new Map(graph.edges.map((edge) => [edge.id, edge])), [graph.edges]);
  const selectedNode = nodeById.get(selectedNodeId) || graph.nodes[0];
  const selectedEdge = edgeById.get(selectedEdgeId);
  const contextNodes = graph.nodes.filter((node) => contextIds.has(node.id));
  const reportSections = useMemo(() => {
    if (industryId !== "fertilizer" || !selectedNode) return [];
    const collectionRefs = new Set<string>();
    const collect = (node?: SourceGraphNode) => {
      if (!node) return;
      if (node.type === "collection" && node.metadata.collection_ref) collectionRefs.add(String(node.metadata.collection_ref));
      node.childIds.map((id) => nodeById.get(id)).filter((child) => child?.type === "collection").forEach((child) => collect(child));
      if (node.type === "publisher" && node.anchorId) collect(nodeById.get(node.anchorId));
    };
    collect(selectedNode);
    return fertilizerReport.sections.filter((section) => section.dependencies.some((dependency) => collectionRefs.has(dependency)));
  }, [industryId, nodeById, selectedNode]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedIndustry = params.get("industry");
    const source = params.get("source");
    if (requestedIndustry && sourceGraphIndustries.some((industry) => industry.id === requestedIndustry)) setIndustryId(requestedIndustry);
    if (!source) return;
    const match = graph.nodes.find((node) => node.type === "collection" && node.metadata.collection_ref === source);
    if (match?.anchorId) {
      setExpandedSignals(new Set([match.anchorId]));
      setSelectedNodeId(match.id);
    }
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    const positions = radialPositions(visible.nodes);
    const elements: ElementDefinition[] = [
      ...visible.nodes.map((node) => ({
        data: { id: node.id, label: shortLabel(node.label), type: node.type },
        position: positions.get(node.id),
      })),
      ...visible.edges.map((edge) => ({
        data: { id: edge.id, source: edge.source, target: edge.target, trust: edge.trust, relation: edge.relation },
      })),
    ];
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      layout: { name: "preset", fit: true, padding: 70 },
      minZoom: 0.35,
      maxZoom: 2.4,
      wheelSensitivity: 0.18,
      style: [
        { selector: "node", style: { "font-family": "IBM Plex Sans, sans-serif", "font-size": 10, "font-weight": 600, label: "data(label)", "text-wrap": "wrap", "text-max-width": 120, "text-valign": "center", "text-halign": "center", "border-width": 1.5, "border-color": "#ffffff", "overlay-opacity": 0, color: "#15252f", "background-color": "#ffffff", width: 105, height: 48 } },
        { selector: 'node[type = "industry"]', style: { shape: "diamond", width: 132, height: 132, "background-color": "#0b5f58", color: "#ffffff", "font-size": 13, "font-weight": 700, "text-max-width": 90 } },
        { selector: 'node[type = "driver"]', style: { shape: "hexagon", width: 120, height: 75, "background-color": "#dce9e5", "border-color": "#83aaa1" } },
        { selector: 'node[type = "signal"]', style: { shape: "ellipse", width: 145, height: 76, "background-color": "#ffffff", "border-color": "#aab9bd", "text-max-width": 112 } },
        { selector: 'node[type = "collection"]', style: { shape: "roundrectangle", width: 150, height: 65, "background-color": "#fff8ea", "border-color": "#d7b36b", "text-max-width": 122 } },
        { selector: 'node[type = "publisher"]', style: { shape: "rectangle", width: 130, height: 50, "background-color": "#e9edf0", "border-color": "#9eabb2", "font-size": 9 } },
        { selector: "node:selected", style: { "border-width": 4, "border-color": "#d28d21", "underlay-color": "#f2d9a9", "underlay-opacity": 0.35, "underlay-padding": 8 } },
        { selector: "edge", style: { width: 1.6, "line-color": "#9badb3", "target-arrow-color": "#9badb3", "target-arrow-shape": "triangle", "arrow-scale": 0.7, "curve-style": "bezier", opacity: 0.78, "overlay-opacity": 0 } },
        { selector: 'edge[trust = "analyst_mapping"]', style: { "line-style": "dashed", "line-dash-pattern": [7, 5], "line-color": "#4d8b82", "target-arrow-color": "#4d8b82" } },
        { selector: 'edge[trust = "catalog_fact"]', style: { width: 2.4, "line-style": "solid", "line-color": "#213f52", "target-arrow-color": "#213f52" } },
        { selector: "edge:selected", style: { width: 4, "line-color": "#d28d21", "target-arrow-color": "#d28d21", opacity: 1 } },
      ],
    });
    cy.on("tap", "node", (event: EventObject) => {
      setSelectedNodeId(event.target.id());
      setSelectedEdgeId("");
    });
    cy.on("tap", "edge", (event: EventObject) => {
      setSelectedEdgeId(event.target.id());
    });
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
  }, [visible.edges, visible.nodes]);

  function toggleType(type: GraphNodeType) {
    if (type === "industry") return;
    setEnabledTypes((current) => {
      const next = new Set(current);
      if (next.has(type)) next.delete(type); else next.add(type);
      return next;
    });
  }

  function chooseIndustry(nextIndustryId: string) {
    setIndustryId(nextIndustryId);
    setExpandedSignals(new Set());
    setExpandedCollections(new Set());
    setSelectedNodeId(`industry:${nextIndustryId}`);
    setSelectedEdgeId("");
    setContextIds(new Set());
  }

  function toggleChildren(node: SourceGraphNode) {
    if (node.type === "signal") {
      setExpandedSignals((current) => {
        const next = new Set(current);
        if (next.has(node.id)) next.delete(node.id); else next.add(node.id);
        return next;
      });
    }
    if (node.type === "collection") {
      setExpandedCollections((current) => {
        const next = new Set(current);
        if (next.has(node.id)) next.delete(node.id); else next.add(node.id);
        return next;
      });
    }
  }

  function focusSearch() {
    const needle = search.trim().toLowerCase();
    if (!needle) return;
    const match = graph.nodes.find((node) => node.label.toLowerCase().includes(needle));
    if (!match) { setToast("No source matches that search"); return; }
    if (match.type === "collection" && match.anchorId) setExpandedSignals((current) => new Set(current).add(match.anchorId as string));
    if (match.type === "publisher" && match.anchorId) {
      const collection = nodeById.get(match.anchorId);
      if (collection?.anchorId) setExpandedSignals((current) => new Set(current).add(collection.anchorId as string));
      setExpandedCollections((current) => new Set(current).add(match.anchorId as string));
    }
    setSelectedNodeId(match.id);
    setSelectedEdgeId("");
    window.setTimeout(() => {
      const element = cyRef.current?.getElementById(match.id);
      if (element?.length) { cyRef.current?.animate({ center: { eles: element }, zoom: 1.25 }, { duration: 350 }); element.select(); }
    }, 120);
  }

  function toggleContext(id: string) {
    setContextIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function copyContext() {
    const groups = typeOrder
      .map((type) => ({ type, nodes: contextNodes.filter((node) => node.type === type) }))
      .filter((group) => group.nodes.length);
    const markdown = [
      `# ${graph.industry.name} source context`,
      "",
      ...groups.flatMap(({ type, nodes }) => [
        `## ${typeLabels[type]}`,
        ...nodes.flatMap((node) => [`- **${node.label}** — ${node.description}`, `  - Path: ${nodeBreadcrumb(node, graph).join(" → ")}`]),
        "",
      ]),
      "## Trust boundary",
      "Solid connections are publisher-to-collection catalog facts. Connections from collections through publication purposes and drivers to the industry are analyst-curated mappings.",
    ].join("\n");
    await navigator.clipboard.writeText(markdown);
    setToast("Context outline copied");
  }

  const detail = selectedEdge ? null : selectedNode;
  return (
    <main className="source-graph-shell">
      <header className="source-graph-header">
        <Link className="industry-map-brand" href="/"><span>L</span><strong>Ledger</strong></Link>
        <div><span>Authoritative source graph</span><strong>See why every publication is here.</strong></div>
        <nav>{industryId === "fertilizer" && <Link href="/industries/fertilizer/report">Fertilizer report</Link>}<Link href="/context-map">Document map</Link><Link href="/industries">Industry packs</Link><Link href="/library">PDF library</Link></nav>
      </header>

      <section className="source-graph-toolbar">
        <label><span>Industry</span><select value={industryId} onChange={(event) => chooseIndustry(event.target.value)}>{sourceGraphIndustries.map((industry) => <option value={industry.id} key={industry.id}>{industry.name} · {industry.collectionCount} publications</option>)}</select></label>
        <form onSubmit={(event) => { event.preventDefault(); focusSearch(); }}><label><span>Find a source</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Publisher or publication…" /></label><button type="submit">Focus</button></form>
        <div className="source-graph-type-filters"><span>Show</span>{typeOrder.slice(1).map((type) => <button className={enabledTypes.has(type) ? "active" : ""} type="button" key={type} onClick={() => toggleType(type)}>{typeLabels[type]} <b>{graph.nodes.filter((node) => node.type === type).length}</b></button>)}</div>
      </section>

      <section className="source-graph-workspace">
        <div className="source-graph-canvas-wrap">
          <div className="source-graph-title"><span>{graph.industry.name}</span><h1>Who publishes the evidence behind this industry?</h1><p>{graph.industry.economic_identity}</p></div>
          <div className="source-graph-legend"><span><i className="solid" />Catalog fact</span><span><i className="dashed" />Analyst mapping</span><small>Click a publication purpose to reveal its families.</small></div>
          <div className="source-graph-canvas" ref={containerRef} aria-label={`${graph.industry.name} authoritative source graph`} />
          <div className="source-graph-canvas-actions"><button type="button" onClick={() => cyRef.current?.fit(undefined, 70)}>Fit graph</button><button type="button" onClick={() => { setExpandedSignals(new Set()); setExpandedCollections(new Set()); }}>Collapse</button></div>
        </div>

        <aside className="source-graph-detail">
          {selectedEdge ? (
            <>
              <span className={`graph-trust trust-${selectedEdge.trust}`}>{selectedEdge.trust === "catalog_fact" ? "Catalog fact" : "Analyst mapping"}</span>
              <h2>{nodeById.get(selectedEdge.source)?.label} <b>→</b> {nodeById.get(selectedEdge.target)?.label}</h2>
              <p>{selectedEdge.reason}</p>
              <dl><div><dt>Relationship</dt><dd>{selectedEdge.relation.replaceAll("_", " ")}</dd></div><div><dt>Connector</dt><dd>{selectedEdge.trust === "catalog_fact" ? "Solid" : "Dashed"}</dd></div></dl>
            </>
          ) : detail ? (
            <>
              <span className={`graph-node-type type-${detail.type}`}>{typeLabels[detail.type]}</span>
              <p className="graph-breadcrumb">{nodeBreadcrumb(detail, graph).join("  ›  ")}</p>
              <h2>{detail.label}</h2>
              <p>{detail.description}</p>
              {Object.keys(detail.metadata).length > 0 && <dl>{Object.entries(detail.metadata).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{Array.isArray(value) ? value.join(" · ") : value}</dd></div>)}</dl>}
              {(detail.type === "signal" || detail.type === "collection") && detail.childIds.length > 0 && <button className="graph-expand-button" type="button" onClick={() => toggleChildren(detail)}>{(detail.type === "signal" ? expandedSignals : expandedCollections).has(detail.id) ? "Hide" : "Reveal"} {detail.childIds.length} {detail.type === "signal" ? "publication families" : "publisher"}</button>}
              <button className={contextIds.has(detail.id) ? "graph-context-button selected" : "graph-context-button"} type="button" onClick={() => toggleContext(detail.id)}>{contextIds.has(detail.id) ? "Remove from context" : "Add this boundary to context"}</button>
              {detail.type === "collection" && <Link className="graph-library-link" href={`/library-preview?collection_ref=${encodeURIComponent(String(detail.metadata.collection_ref || ""))}`}>Browse documents in this publication →</Link>}
              {reportSections.length > 0 && (
                <section className="graph-report-impact">
                  <header><span>Used in fertilizer report</span><strong>{reportSections.length}</strong></header>
                  <p>These research questions are regenerated when this publication family supplies new accepted evidence.</p>
                  {reportSections.map((section) => <Link href={`/industries/fertilizer/report?section=${section.id}`} key={section.id}><strong>{section.title}</strong><small>{section.page_count ? `${section.page_count} selected pages` : "Derived answer"}</small></Link>)}
                </section>
              )}
            </>
          ) : null}

          <section className="source-graph-context">
            <header><span>Analyst context</span><strong>{contextNodes.length}</strong></header>
            {contextNodes.length ? <div>{contextNodes.map((node) => <button type="button" key={node.id} onClick={() => { setSelectedNodeId(node.id); setSelectedEdgeId(""); }}>{typeLabels[node.type]}<strong>{node.label}</strong><i onClick={(event) => { event.stopPropagation(); toggleContext(node.id); }}>×</i></button>)}</div> : <p>Select an industry question, source purpose or publication family. Each stays a separate context boundary.</p>}
            <button disabled={!contextNodes.length} type="button" onClick={() => void copyContext()}>Copy structured outline</button>
          </section>
        </aside>
      </section>

      <section className="source-graph-mobile">
        <header><span>{graph.industry.name}</span><h1>Browse the source chain</h1><p>On smaller screens the same graph becomes an ordered path.</p></header>
        {graph.nodes.filter((node) => node.type === "driver").map((driver) => (
          <details key={driver.id}><summary><strong>{driver.label}</strong><span>{driver.childIds.length} purposes</span></summary><div>{driver.childIds.map((signalId) => nodeById.get(signalId)).filter(Boolean).map((signal) => <details key={signal?.id}><summary>{signal?.label}</summary><div>{signal?.childIds.map((collectionId) => nodeById.get(collectionId)).filter(Boolean).map((collection) => <button type="button" key={collection?.id} onClick={() => { if (collection) { setSelectedNodeId(collection.id); toggleContext(collection.id); } }}>{collection?.label}<small>{collection?.metadata.publisher}</small></button>)}</div></details>)}</div></details>
        ))}
      </section>
      {toast && <div className="source-graph-toast" onAnimationEnd={() => setToast("")}>{toast}</div>}
    </main>
  );
}
