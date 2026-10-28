"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import report from "../../../../data/fertilizer-report.generated.json";

type ReportSection = (typeof report.sections)[number];
type Source = (typeof report.source_index)[keyof typeof report.source_index];

const groups = [
  { id: "summary", label: "Conclusion", sections: ["executive_summary"] },
  { id: "sector", label: "Sector", sections: ["market_balance", "demand_affordability", "supply_gas_capacity", "price_margin_transmission"] },
  { id: "companies", label: "Companies", sections: ["ffc_operations", "ffc_capital_allocation", "efert_company", "fatima_company", "agritech_company"] },
  { id: "synthesis", label: "Synthesis", sections: ["company_comparison", "thesis_monitoring"] },
] as const;

const sectionMap = new Map(report.sections.map((section) => [section.id, section]));
const generationOrder = groups.flatMap((group) => group.sections).filter((id) => id !== "executive_summary").concat("executive_summary");

function citationLinks(markdown: string) {
  return markdown.replace(/\[(P\d{3})\]/g, "[$1](#source-$1)");
}

function cleanExcerpt(value: string) {
  return value.replace(/^<!-- Page \d+ -->\s*/i, "").trim();
}

function Markdown({ value, onSource }: { value: string; onSource?: (id: string) => void }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => href?.startsWith("#source-")
      ? <button className="report-citation" type="button" onClick={() => onSource?.(href.replace("#source-", ""))}>{children}</button>
      : <a href={href} target="_blank" rel="noreferrer">{children}</a>,
    table: ({ children }) => <div className="report-table-wrap"><table>{children}</table></div>,
  }}>{citationLinks(value)}</ReactMarkdown>;
}

function sectionPublishers(section: ReportSection) {
  return [...new Set(section.page_ids.map((id) => report.source_index[id as keyof typeof report.source_index]?.publisher).filter(Boolean))];
}

export default function FertilizerReportClient() {
  const [activeId, setActiveId] = useState("market_balance");
  const [generatedIds, setGeneratedIds] = useState<Set<string>>(new Set());
  const [generatingIds, setGeneratingIds] = useState<Set<string>>(new Set());
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [contextOpen, setContextOpen] = useState(true);
  const [copied, setCopied] = useState("");
  const [hydrated, setHydrated] = useState(false);

  const activeSection = sectionMap.get(activeId) || report.sections[0];
  const selectedSource = selectedSourceId ? report.source_index[selectedSourceId as keyof typeof report.source_index] as Source : null;
  const activeSources = activeSection.page_ids.map((id) => ({ id, ...report.source_index[id as keyof typeof report.source_index] })).filter((source) => source.publisher);
  const generatedSections = generationOrder.map((id) => sectionMap.get(id)).filter((section): section is ReportSection => Boolean(section && generatedIds.has(section.id)));
  const progress = Math.round((generatedIds.size / report.sections.length) * 100);
  const activeGenerated = generatedIds.has(activeSection.id);
  const activeGenerating = generatingIds.has(activeSection.id);

  useEffect(() => {
    let saved: { active?: string; generated?: string[] } = {};
    try { saved = JSON.parse(window.localStorage.getItem("ledger:fertilizer-interactive-report:v1") || "{}"); } catch { /* optional local state */ }
    queueMicrotask(() => {
      if (saved.active && sectionMap.has(saved.active)) setActiveId(saved.active);
      if (saved.generated?.length) setGeneratedIds(new Set(saved.generated.filter((id) => sectionMap.has(id))));
      setHydrated(true);
    });
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    window.localStorage.setItem("ledger:fertilizer-interactive-report:v1", JSON.stringify({ active: activeId, generated: [...generatedIds] }));
  }, [activeId, generatedIds, hydrated]);

  function openSection(id: string) {
    setActiveId(id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function generateSection(id: string) {
    setGeneratingIds((current) => new Set(current).add(id));
    window.setTimeout(() => {
      setGeneratedIds((current) => new Set(current).add(id));
      setGeneratingIds((current) => { const next = new Set(current); next.delete(id); return next; });
    }, 650);
  }

  function removeSection(id: string) {
    setGeneratedIds((current) => { const next = new Set(current); next.delete(id); return next; });
  }

  function generateRemaining() {
    const pending = generationOrder.filter((id) => !generatedIds.has(id));
    setGeneratingIds(new Set(pending));
    pending.forEach((id, index) => window.setTimeout(() => {
      setGeneratedIds((current) => new Set(current).add(id));
      setGeneratingIds((current) => { const next = new Set(current); next.delete(id); return next; });
    }, 300 + index * 180));
  }

  const assembledMarkdown = useMemo(() => [
    "# Pakistan fertilizer — interactive industry report",
    "",
    `*Evidence pack as of ${report.pack_as_of}. ${generatedSections.length} of ${report.sections.length} segments assembled.*`,
    "",
    ...generatedSections.flatMap((section) => [section.answer, "", "---", ""]),
  ].join("\n"), [generatedSections]);

  async function copyText(value: string, key: string) {
    await navigator.clipboard.writeText(value);
    setCopied(key);
    window.setTimeout(() => setCopied(""), 1400);
  }

  const publishers = sectionPublishers(activeSection);

  return <main className="interactive-report-shell">
    <header className="interactive-report-topbar">
      <Link className="industry-map-brand" href="/"><span>L</span><strong>Ledger</strong></Link>
      <div><span>Interactive report</span><b>Pakistan fertilizer</b></div>
      <nav><Link href="/source-graph?industry=fertilizer">Explore source map</Link><button onClick={() => void copyText(assembledMarkdown, "report")} disabled={!generatedSections.length}>{copied === "report" ? "Copied" : "Copy assembled report"}</button></nav>
    </header>

    <div className="interactive-report-layout">
      <aside className="report-outline">
        <header><span>Report outline</span><strong>{generatedIds.size}/{report.sections.length} generated</strong></header>
        <div className="report-progress"><i style={{ width: `${progress}%` }} /></div>
        <p>Generate one segment at a time. Each uses its own bounded source packet.</p>
        {groups.map((group) => <section key={group.id}>
          <h2>{group.label}</h2>
          {group.sections.map((id) => {
            const section = sectionMap.get(id)!;
            const isGenerated = generatedIds.has(id);
            const isGenerating = generatingIds.has(id);
            return <button className={activeId === id ? "active" : ""} key={id} onClick={() => openSection(id)}>
              <span className={isGenerating ? "working" : isGenerated ? "done" : "ready"}>{isGenerating ? "…" : isGenerated ? "✓" : ""}</span>
              <span><strong>{section.title}</strong><small>{section.kind === "atomic" ? `${section.page_count} source pages` : `${section.upstream.length} upstream segments`}</small></span>
            </button>;
          })}
        </section>)}
        <button className="generate-all-button" onClick={generateRemaining} disabled={generatedIds.size === report.sections.length || generatingIds.size > 0}>{generatedIds.size === report.sections.length ? "Report complete" : `Generate remaining ${report.sections.length - generatedIds.size}`}</button>
      </aside>

      <article className="report-segment-canvas">
        <header className="segment-header">
          <div><span className="segment-kicker">{activeSection.group} segment · {activeSection.kind === "atomic" ? `${activeSection.page_count} selected pages` : `synthesizes ${activeSection.upstream.length} upstream segments`}</span><h1>{activeSection.title}</h1><p>{activeSection.question}</p></div>
          <div className="segment-actions">
            <button className="context-toggle" onClick={() => setContextOpen((value) => !value)}>{contextOpen ? "Hide" : "Show"} context</button>
            <button className={activeGenerated ? "context-toggle" : "generate-segment-button"} onClick={() => activeGenerated ? removeSection(activeSection.id) : generateSection(activeSection.id)} disabled={activeGenerating}>{activeGenerating ? "Generating…" : activeGenerated ? "Remove from report" : "Generate segment"}</button>
          </div>
        </header>

        <section className="segment-context-strip">
          <div><span>Evidence</span><strong>{activeSection.kind === "atomic" ? `${activeSection.page_count} physical pages` : `${activeSection.upstream.length} generated segments`}</strong></div>
          <div><span>Sources</span><strong>{publishers.length ? publishers.slice(0, 2).join(" · ") : "Derived synthesis"}</strong></div>
          <div><span>Refresh</span><strong>{activeSection.monitors[0] || "When an upstream segment changes"}</strong></div>
        </section>

        {!activeGenerated && !activeGenerating && <section className="segment-empty-state">
          <span>{activeSection.kind === "atomic" ? activeSection.page_count : activeSection.upstream.length}</span>
          <h2>{activeSection.kind === "atomic" ? "The context packet is ready." : "The upstream answers are ready to synthesize."}</h2>
          <p>{activeSection.kind === "atomic" ? "Generate this segment from only the selected authoritative pages. The answer will distinguish direct evidence, inference, contradictions and unknowns." : "Generate this segment from the completed answers rather than resending the full source pack."}</p>
          <button onClick={() => generateSection(activeSection.id)}>Generate {activeSection.title.toLowerCase()}</button>
        </section>}

        {activeGenerating && <section className="segment-generating-state"><i /><h2>Generating {activeSection.title.toLowerCase()}…</h2><p>Using the bounded context shown alongside this segment.</p></section>}

        {activeGenerated && !activeGenerating && <section className="generated-segment">
          <header><div><span>Generated segment</span><strong>DeepSeek · pack checkpoint {report.pack_as_of}</strong></div><button onClick={() => void copyText(activeSection.answer, activeSection.id)}>{copied === activeSection.id ? "Copied" : "Copy Markdown"}</button></header>
          <div className="generated-segment-markdown"><Markdown value={activeSection.answer} onSource={setSelectedSourceId} /></div>
          <footer><span>{activeSection.word_count.toLocaleString()} words</span><button onClick={() => { const index = generationOrder.indexOf(activeSection.id); openSection(generationOrder[Math.min(index + 1, generationOrder.length - 1)]); }}>Next segment →</button></footer>
        </section>}
      </article>

      {contextOpen && <aside className="segment-context-panel">
        <header><span>Context for this segment</span><strong>{activeSection.kind === "atomic" ? `${activeSources.length} pages` : `${activeSection.upstream.length} answers`}</strong></header>
        {activeSection.kind === "atomic" ? <>
          <p>These pages—not the entire 100-page pack—are sent for this answer.</p>
          <div className="context-publisher-list">{publishers.map((publisher) => <span key={publisher}>{publisher}</span>)}</div>
          <div className="context-source-list">{activeSources.map((source) => <button key={source.id} onClick={() => setSelectedSourceId(source.id)}><b>{source.id}</b><span><strong>{source.document_title}</strong><small>Physical page {source.physical_page}</small></span></button>)}</div>
        </> : <>
          <p>This synthesis uses completed report segments as context.</p>
          <div className="upstream-segment-list">{activeSection.upstream.map((id) => { const upstream = sectionMap.get(id); return <button key={id} onClick={() => openSection(id)}><span className={generatedIds.has(id) ? "done" : "ready"}>{generatedIds.has(id) ? "✓" : ""}</span><strong>{upstream?.title || id}</strong></button>; })}</div>
        </>}
        <footer><Link href={`/?q=${encodeURIComponent(activeSection.question)}`}>Search beyond this packet →</Link></footer>
      </aside>}
    </div>

    <footer className="assembled-report-bar"><div><span>Assembled report</span><strong>{generatedIds.size} of {report.sections.length} segments · {progress}%</strong></div><div className="report-progress"><i style={{ width: `${progress}%` }} /></div><button onClick={generateRemaining} disabled={generatedIds.size === report.sections.length || generatingIds.size > 0}>{generatedIds.size === report.sections.length ? "All segments generated" : "Generate remaining"}</button><button className="copy-report" onClick={() => void copyText(assembledMarkdown, "report")} disabled={!generatedSections.length}>{copied === "report" ? "Copied" : "Copy report"}</button></footer>

    {selectedSource && <div className="source-drawer-backdrop" onClick={() => setSelectedSourceId("")}><aside className="source-drawer" onClick={(event) => event.stopPropagation()}><button className="drawer-close" onClick={() => setSelectedSourceId("")}>×</button><span className="eyebrow">Physical evidence · {selectedSourceId}</span><h2>{selectedSource.document_title}</h2><p>{selectedSource.publisher} · physical page {selectedSource.physical_page}</p><div className="source-markdown"><Markdown value={cleanExcerpt(selectedSource.excerpt)} /></div></aside></div>}
  </main>;
}
