"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CORPORA,
  CORPUS_LABELS,
  type CorpusKind,
  type DocumentDetail,
  type InclusionMode,
  type QualityFlag,
  type SearchResponse,
  type SearchResult,
  type StackedSource,
  type StatsResponse,
  compactNumber,
  formatScore,
  groupByEntity,
  highlightParts,
  sourceKey,
  worstSeverity,
} from "@/lib/corpus";
import {
  type AppliedSearch,
  buildContextMarkdown,
  buildContextPayload,
  estimateTokens,
  selectedContent,
} from "@/lib/context";
import {
  DEFAULT_PROVIDER,
  PROVIDERS,
  type CompileResponse,
  type ProviderId,
  estimateCostUsd,
  formatUsd,
} from "@/lib/providers";
import { type PeriodPreset, describeScope, inferSearch } from "@/lib/query";
import { TERM_GROUPS, adviseQuery } from "@/lib/vocabulary";
import { capPerEntity, coverage, dedupeAdjacent, latestFilingOnly } from "@/lib/shaping";
import { collapseRecurring, familyMembers } from "@/lib/recurring";
import { loadDocument } from "@/lib/document-cache";
import {
  type Scrapbook,
  describeScrapbook,
  emptyScrapbook,
  fromClipped,
  loadActiveId,
  loadScrapbooks,
  relativeTime,
  saveActiveId,
  saveScrapbooks,
  toClipped,
} from "@/lib/scrapbook";
import {
  buildScrapbookFile,
  buildSourceList,
  downloadFile,
  mergeScrapbooks,
  parseScrapbookFile,
  slugify,
} from "@/lib/export";
import DocumentReader from "@/components/DocumentReader";

type PanelView = "context" | "send";

const CURRENT_YEAR = new Date().getFullYear();
const ALL_CORPORA: CorpusKind[] = CORPORA.map((corpus) => corpus.id);

// Written with high-signal vocabulary, so the first thing a new user runs
// demonstrates what a good query looks like.
const EXAMPLES = [
  "UBL cost of funds and cost to income last 3 years",
  "HUBC circular debt and trade debts",
  "cement demand and capacity utilization",
];

function Excerpt({ text }: { text: string }) {
  return (
    <>
      {highlightParts(text).map((part, index) =>
        part.marked ? <mark key={index}>{part.text}</mark> : <span key={index}>{part.text}</span>,
      )}
    </>
  );
}

function flagLabel(flag: QualityFlag): string {
  const head = flag.rule || flag.metric || flag.category || flag.flag_id;
  return flag.message ? `${head} — ${flag.message}` : head;
}

export default function Home() {
  const [scrapbooks, setScrapbooks] = useState<Scrapbook[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [hydrated, setHydrated] = useState(false);
  const [bookMenuOpen, setBookMenuOpen] = useState(false);
  const [sessionLabel, setSessionLabel] = useState("New research session");

  const [query, setQuery] = useState("");
  const [scopeOpen, setScopeOpen] = useState(false);
  const [termsOpen, setTermsOpen] = useState(false);
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [tickerInput, setTickerInput] = useState("");
  const [tickerOptions, setTickerOptions] = useState<string[]>([]);
  const [selectedTickers, setSelectedTickers] = useState<string[]>([]);
  const [periodPreset, setPeriodPreset] = useState<PeriodPreset>("all");
  const [fromYear, setFromYear] = useState(String(CURRENT_YEAR - 2));
  const [toYear, setToYear] = useState(String(CURRENT_YEAR));
  const [corpora, setCorpora] = useState<CorpusKind[]>(ALL_CORPORA);

  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [configuredProviders, setConfiguredProviders] = useState<Set<ProviderId>>(new Set());
  const [appliedSearch, setAppliedSearch] = useState<AppliedSearch | null>(null);
  const [search, setSearch] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [reading, setReading] = useState<SearchResult | null>(null);
  /** Which year of a recurring disclosure the researcher is looking at. */
  const [chosenYear, setChosenYear] = useState<Record<string, number>>({});

  const [stack, setStack] = useState<Record<string, StackedSource>>({});
  const [panelView, setPanelView] = useState<PanelView>("context");
  const [panelOpen, setPanelOpen] = useState(false);

  const [provider, setProvider] = useState<ProviderId>(DEFAULT_PROVIDER);
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);
  const [compiled, setCompiled] = useState<CompileResponse | null>(null);
  const [compileError, setCompileError] = useState("");
  const [toast, setToast] = useState("");
  const saveTimer = useRef<number | null>(null);

  const stackedSources = useMemo(() => Object.values(stack), [stack]);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 1800);
  }, []);

  /** Refills page text for clips restored from storage, which store only identity. */
  const rehydrate = useCallback(async (sources: StackedSource[]) => {
    await Promise.all(
      sources.map(async (source) => {
        const detail = await loadDocument(source.corpus, source.id);
        setStack((current) => {
          const key = sourceKey(source);
          if (!current[key]) return current;
          return {
            ...current,
            [key]: {
              ...current[key],
              loading: false,
              detail: detail || undefined,
              inclusionMode: detail ? current[key].inclusionMode : "summary",
            },
          };
        });
      }),
    );
  }, []);

  const openScrapbook = useCallback(
    (book: Scrapbook) => {
      setActiveId(book.id);
      saveActiveId(book.id);
      setSessionLabel(book.name);
      setPrompt(book.prompt || "");
      const restored = book.pages.map(fromClipped);
      setStack(Object.fromEntries(restored.map((source) => [sourceKey(source), source])));
      setReading(null);
      setPanelView("context");
      setCompiled(null);
      if (restored.length) void rehydrate(restored);
    },
    [rehydrate],
  );

  // Restore the last scrapbook on mount.
  //
  // localStorage does not exist while this renders on the server, so the read
  // cannot happen in a state initialiser and has to be an effect. It runs once
  // and React batches the updates, so the cascading-render concern behind the
  // lint rule does not apply here.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const stored = loadScrapbooks();
    if (stored.length === 0) {
      const fresh = emptyScrapbook();
      saveScrapbooks([fresh]);
      saveActiveId(fresh.id);
      setScrapbooks([fresh]);
      setActiveId(fresh.id);
      setHydrated(true);
      return;
    }
    setScrapbooks(stored);
    const wanted = loadActiveId();
    openScrapbook(stored.find((entry) => entry.id === wanted) || stored[0]);
    setHydrated(true);
  }, [openScrapbook]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Persist the working scrapbook shortly after any change to it.
  useEffect(() => {
    if (!hydrated || !activeId) return;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      setScrapbooks((current) => {
        const next = current.map((book) =>
          book.id === activeId
            ? {
                ...book,
                name: sessionLabel,
                prompt,
                pages: stackedSources.map(toClipped),
                updatedAt: new Date().toISOString(),
              }
            : book,
        );
        saveScrapbooks(next);
        return next;
      });
    }, 400);
    return () => {
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    };
  }, [hydrated, activeId, sessionLabel, prompt, stackedSources]);

  // Independent fetches, so one unavailable endpoint cannot blank the others.
  useEffect(() => {
    fetch("/api/stats")
      .then((response) => (response.ok ? response.json() : null))
      .then((data: StatsResponse | null) => setStats(data))
      .catch(() => setStats(null));

    fetch("/api/tickers")
      .then((response) => (response.ok ? response.json() : { tickers: [] }))
      .then((data: { tickers?: string[] }) => setTickerOptions(data.tickers || []))
      .catch(() => setTickerOptions([]));

    fetch("/api/providers")
      .then((response) => (response.ok ? response.json() : { providers: [] }))
      .then((data: { providers?: Array<{ id: ProviderId; configured: boolean }> }) => {
        const available = new Set(
          (data.providers || []).filter((entry) => entry.configured).map((entry) => entry.id),
        );
        setConfiguredProviders(available);
        setProvider((current) => (available.has(current) ? current : [...available][0] || current));
      })
      .catch(() => setConfiguredProviders(new Set()));
  }, []);

  const stackGroups = useMemo(() => groupByEntity(stackedSources), [stackedSources]);
  const resultGroups = useMemo(() => groupByEntity(search?.results || []), [search]);
  const knownTickers = useMemo(() => new Set(tickerOptions), [tickerOptions]);
  const advice = useMemo(() => adviseQuery(query), [query]);
  const stackCoverage = useMemo(() => coverage(stackedSources), [stackedSources]);

  const markdown = useMemo(
    () => buildContextMarkdown(sessionLabel, prompt, stackedSources),
    [sessionLabel, prompt, stackedSources],
  );
  const payload = useMemo(
    () =>
      buildContextPayload({
        sessionLabel,
        prompt,
        provider,
        retrieval: appliedSearch,
        sources: stackedSources,
        markdown,
      }),
    [appliedSearch, markdown, prompt, provider, sessionLabel, stackedSources],
  );

  const tokenEstimate = estimateTokens(markdown.length);
  const activeProvider = PROVIDERS.find((entry) => entry.id === provider) || PROVIDERS[0];

  const isInContext = useCallback(
    (corpus: CorpusKind, id: number) => Boolean(stack[`${corpus}:${id}`]),
    [stack],
  );

  async function runSearch(event: FormEvent) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;

    const interpreted = inferSearch({
      rawQuery: trimmed,
      selectedTickers,
      knownTickers,
      periodPreset,
      fromYear,
      toYear,
      selectedCorpora: corpora,
      currentYear: CURRENT_YEAR,
    });

    setSelectedTickers(interpreted.tickers);
    setCorpora(interpreted.corpora as CorpusKind[]);
    if (interpreted.dateFrom && interpreted.dateTo && periodPreset === "all") {
      setFromYear(interpreted.dateFrom.slice(0, 4));
      setToYear(interpreted.dateTo.slice(0, 4));
      setPeriodPreset("custom");
    }
    if (!prompt.trim()) setPrompt(trimmed);

    setAppliedSearch(interpreted);
    setSearching(true);
    setSearchError("");
    setReading(null);

    const params = new URLSearchParams({
      q: interpreted.lexicalQuery,
      limit: "6",
      corpora: interpreted.corpora.join(","),
    });
    if (interpreted.tickers.length) params.set("tickers", interpreted.tickers.join(","));
    if (interpreted.dateFrom) params.set("date_from", interpreted.dateFrom);
    if (interpreted.dateTo) params.set("date_to", interpreted.dateTo);

    try {
      const response = await fetch(`/api/search?${params.toString()}`);
      const data = (await response.json()) as SearchResponse;
      if (!response.ok) throw new Error(data.error || "Search failed.");
      setSearch(data);
    } catch (error) {
      setSearch(null);
      setSearchError(error instanceof Error ? error.message : "Search failed.");
    } finally {
      setSearching(false);
    }
  }

  function addTerm(term: string) {
    const current = query.trim();
    if (current.toLowerCase().includes(term.toLowerCase())) return;
    setQuery(current ? `${current} ${term}` : term);
  }

  function addTicker() {
    const ticker = tickerInput.trim().toUpperCase();
    if (!ticker || selectedTickers.includes(ticker)) return;
    setSelectedTickers((current) => [...current, ticker]);
    setTickerInput("");
  }

  function toggleCorpus(corpus: CorpusKind) {
    setCorpora((current) => {
      // Never allow an empty set: the worker silently falls back to filings.
      if (current.includes(corpus)) {
        return current.length === 1 ? current : current.filter((value) => value !== corpus);
      }
      return [...current, corpus];
    });
  }

  function removeFromContext(corpus: CorpusKind, id: number) {
    setStack((current) => {
      const next = { ...current };
      delete next[`${corpus}:${id}`];
      return next;
    });
  }

  /**
   * Clips any page of a filing, whether it was a search hit or reached by
   * reading. A page arrived at by reading carries no BM25 score, because it was
   * never ranked by the query.
   */
  function clipPage(
    origin: SearchResult,
    id: number,
    detail: DocumentDetail,
    pageNumber: number | null,
  ) {
    const isAnchor = id === origin.id;
    const meta = detail.metadata || {};
    setStack((current) => ({
      ...current,
      [`${origin.corpus}:${id}`]: {
        ...origin,
        id,
        page_number: pageNumber,
        title: isAnchor ? origin.title : `${origin.entity} — page ${pageNumber ?? "?"}`,
        summary: isAnchor ? origin.summary : String(meta.search_text || ""),
        snippet: isAnchor ? origin.snippet : "",
        bm25_score: isAnchor ? origin.bm25_score : 0,
        source_url: detail.source_url,
        thumbnail_url: detail.thumbnail_url,
        asset_key: detail.asset.key,
        quality_flags: detail.quality_flags || origin.quality_flags,
        loading: false,
        detail,
        inclusionMode: "full",
      },
    }));
  }

  /** Clip straight from a result row, without opening the document. */
  async function quickToggle(source: SearchResult) {
    const key = sourceKey(source);
    if (stack[key]) {
      removeFromContext(source.corpus, source.id);
      return;
    }
    setStack((current) => ({
      ...current,
      [key]: { ...source, loading: true, inclusionMode: "full" },
    }));
    const detail = await loadDocument(source.corpus, source.id);
    setStack((current) => {
      if (!current[key]) return current;
      return {
        ...current,
        [key]: detail
          ? { ...current[key], loading: false, detail }
          : { ...current[key], loading: false, inclusionMode: "summary" },
      };
    });
    if (!detail) showToast("Full page unavailable — using summary");
  }

  function updateMode(source: StackedSource, inclusionMode: InclusionMode) {
    setStack((current) => ({
      ...current,
      [sourceKey(source)]: { ...current[sourceKey(source)], inclusionMode },
    }));
  }

  function removeGroup(entity: string) {
    setStack((current) =>
      Object.fromEntries(Object.entries(current).filter(([, source]) => source.entity !== entity)),
    );
  }

  function reshape(next: StackedSource[], label: string) {
    const removed = stackedSources.length - next.length;
    setStack(Object.fromEntries(next.map((source) => [sourceKey(source), source])));
    showToast(removed > 0 ? `${label}: removed ${removed}` : `${label}: nothing to remove`);
  }

  function newScrapbook() {
    const fresh = emptyScrapbook(`Session ${scrapbooks.length + 1}`);
    const next = [fresh, ...scrapbooks];
    setScrapbooks(next);
    saveScrapbooks(next);
    openScrapbook(fresh);
    setBookMenuOpen(false);
  }

  /** Copies the current pack, for rolling an evidence file into a new period. */
  function duplicateScrapbook() {
    const source = scrapbooks.find((book) => book.id === activeId);
    if (!source) return;
    const now = new Date().toISOString();
    const copy = {
      ...source,
      id: `sb_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
      name: `${source.name} (copy)`,
      createdAt: now,
      updatedAt: now,
    };
    const next = [copy, ...scrapbooks];
    setScrapbooks(next);
    saveScrapbooks(next);
    openScrapbook(copy);
    setBookMenuOpen(false);
  }

  function exportAllScrapbooks() {
    downloadFile(
      `ledger-scrapbooks-${new Date().toISOString().slice(0, 10)}.json`,
      buildScrapbookFile(scrapbooks),
      "application/json",
    );
    setBookMenuOpen(false);
  }

  async function importScrapbooks(file: File) {
    const incoming = parseScrapbookFile(await file.text());
    if (incoming.length === 0) {
      showToast("No scrapbooks found in that file");
      return;
    }
    const next = mergeScrapbooks(scrapbooks, incoming);
    setScrapbooks(next);
    saveScrapbooks(next);
    openScrapbook(next[0]);
    setBookMenuOpen(false);
    showToast(`Imported ${incoming.length} scrapbook${incoming.length === 1 ? "" : "s"}`);
  }

  function deleteScrapbook(id: string) {
    const next = scrapbooks.filter((book) => book.id !== id);
    const list = next.length ? next : [emptyScrapbook()];
    setScrapbooks(list);
    saveScrapbooks(list);
    if (id === activeId) openScrapbook(list[0]);
    setBookMenuOpen(false);
  }

  async function copyText(text: string, message: string) {
    try {
      await navigator.clipboard.writeText(text);
      showToast(message);
    } catch {
      showToast("Copy blocked by the browser");
    }
  }

  async function compileAnswer() {
    if (!stackedSources.length || !prompt.trim()) return;
    setSending(true);
    setCompileError("");
    setCompiled(null);
    try {
      const response = await fetch("/api/compile", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = (await response.json()) as CompileResponse & { detail?: string };
      if (!response.ok) throw new Error([data.error, data.detail].filter(Boolean).join(" "));
      setCompiled(data);
    } catch (error) {
      setCompileError(error instanceof Error ? error.message : "Answer compilation failed.");
    } finally {
      setSending(false);
    }
  }

  const scopeSummary = appliedSearch
    ? describeScope(appliedSearch, CORPUS_LABELS)
    : describeScope(
        {
          naturalQuery: "",
          lexicalQuery: "",
          tickers: selectedTickers,
          dateFrom: null,
          dateTo: null,
          corpora,
        },
        CORPUS_LABELS,
      );

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-row">
          <div className="brand">Ledger</div>

          <div className="book-menu">
            <button
              type="button"
              className="book-trigger"
              onClick={() => setBookMenuOpen((open) => !open)}
              aria-expanded={bookMenuOpen}
            >
              {sessionLabel}
              <i>▾</i>
            </button>

            {bookMenuOpen && (
              <>
                <div className="book-scrim" onClick={() => setBookMenuOpen(false)} />
                <div className="book-pop">
                  <label className="book-label" htmlFor="book-name">
                    Scrapbook name
                  </label>
                  <input
                    id="book-name"
                    className="book-name"
                    value={sessionLabel}
                    onChange={(event) => setSessionLabel(event.target.value)}
                  />

                  <div className="book-label">All scrapbooks</div>
                  <div className="book-list">
                    {scrapbooks.map((book) => (
                      <div
                        className={`book-row ${book.id === activeId ? "on" : ""}`}
                        key={book.id}
                      >
                        <button type="button" onClick={() => { openScrapbook(book); setBookMenuOpen(false); }}>
                          <b>{book.name}</b>
                          <span>
                            {describeScrapbook(book)} · {relativeTime(book.updatedAt)}
                          </span>
                        </button>
                        <button
                          type="button"
                          className="book-delete"
                          aria-label={`Delete ${book.name}`}
                          onClick={() => deleteScrapbook(book.id)}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>

                  <div className="book-actions">
                    <button type="button" onClick={newScrapbook}>
                      + New
                    </button>
                    <button type="button" onClick={duplicateScrapbook}>
                      Duplicate
                    </button>
                    <button type="button" onClick={exportAllScrapbooks}>
                      Export all
                    </button>
                    <label className="book-import">
                      Import
                      <input
                        type="file"
                        accept="application/json,.json"
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          event.target.value = "";
                          if (file) void importScrapbooks(file);
                        }}
                      />
                    </label>
                  </div>
                  <p className="book-note">
                    Saved in this browser only. Export to move a pack to another machine or hand it
                    to a colleague.
                  </p>
                </div>
              </>
            )}
          </div>

          {stats && (
            <span className="corpus-count">
              {compactNumber(stats.total_documents)} pages · BM25
            </span>
          )}
        </div>

        <form className="search-form" onSubmit={runSearch}>
          <div className="search-field">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search authoritative sources…"
              aria-label="Search authoritative sources"
            />
          </div>
          <button className="search-submit" type="submit" disabled={!query.trim() || searching}>
            {searching ? "…" : "Search"}
          </button>
        </form>

        <div className="scope-strip">
          <button
            type="button"
            className="scope-summary"
            onClick={() => setScopeOpen((open) => !open)}
            aria-expanded={scopeOpen}
          >
            {scopeSummary}
          </button>
          <button
            type="button"
            className="terms-toggle"
            onClick={() => setTermsOpen((open) => !open)}
            aria-expanded={termsOpen}
          >
            {termsOpen ? "hide terms" : "search terms"}
          </button>
        </div>

        {advice.weak.length > 0 && (
          <div className="advice">
            <div className="advice-head">These terms match boilerplate across every filing</div>
            {advice.weak.slice(0, 2).map((entry) => (
              <div className="advice-row" key={entry.term}>
                <b>{entry.term}</b> → {entry.matches}
              </div>
            ))}
            {advice.suggestions.length > 0 && (
              <div className="advice-fix">
                try instead:{" "}
                {advice.suggestions.map((term) => (
                  <button type="button" key={term} onClick={() => addTerm(term)}>
                    {term}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {termsOpen && (
          <section className="terms-panel" aria-label="High-signal search terms">
            <p className="terms-note">
              These terms actually appear in the indexed page summaries. Searching for what a page{" "}
              <em>discusses</em> beats searching for the section it sits in.
            </p>
            <div className="terms-columns">
              {TERM_GROUPS.map((group) => {
                const open = openGroup === group.id;
                return (
                  <div className="term-group" key={group.id}>
                    <button
                      type="button"
                      className="term-group-head"
                      onClick={() => setOpenGroup(open ? null : group.id)}
                      aria-expanded={open}
                    >
                      <span>
                        {group.label}
                        {group.sector && <em>sector</em>}
                      </span>
                      <b>{open ? "−" : "+"}</b>
                    </button>
                    {open && (
                      <>
                        <div className="term-hint">{group.hint}</div>
                        <div className="term-chips">
                          {group.terms.map((term) => (
                            <button type="button" key={term} onClick={() => addTerm(term)}>
                              {term}
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {scopeOpen && (
          <section className="scope-panel" aria-label="Search scope">
            <div className="scope-grid">
              <div>
                <label className="scope-label" htmlFor="ticker-filter">
                  Tickers
                </label>
                <div className="ticker-entry">
                  <input
                    id="ticker-filter"
                    list="ticker-options"
                    value={tickerInput}
                    onChange={(event) => setTickerInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        addTicker();
                      }
                    }}
                    placeholder="UBL, HBL…"
                  />
                  <datalist id="ticker-options">
                    {tickerOptions.map((ticker) => (
                      <option value={ticker} key={ticker} />
                    ))}
                  </datalist>
                  <button type="button" onClick={addTicker}>
                    add
                  </button>
                </div>
                {selectedTickers.length > 0 && (
                  <div className="chip-row">
                    {selectedTickers.map((ticker) => (
                      <button
                        type="button"
                        className="chip"
                        key={ticker}
                        onClick={() =>
                          setSelectedTickers((current) => current.filter((v) => v !== ticker))
                        }
                      >
                        {ticker} ×
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <label className="scope-label" htmlFor="period-filter">
                  Period
                </label>
                <select
                  id="period-filter"
                  className="scope-select"
                  value={periodPreset}
                  onChange={(event) => setPeriodPreset(event.target.value as PeriodPreset)}
                >
                  <option value="all">All periods</option>
                  <option value="3y">Last 3 years</option>
                  <option value="5y">Last 5 years</option>
                  <option value="custom">Custom years</option>
                </select>
                {periodPreset === "custom" && (
                  <div className="year-range">
                    <input
                      className="year-input"
                      type="number"
                      value={fromYear}
                      onChange={(event) => setFromYear(event.target.value)}
                      aria-label="From year"
                    />
                    <input
                      className="year-input"
                      type="number"
                      value={toYear}
                      onChange={(event) => setToYear(event.target.value)}
                      aria-label="To year"
                    />
                  </div>
                )}
              </div>

              <div>
                <span className="scope-label">Corpus</span>
                <div className="corpus-toggles">
                  {CORPORA.map((corpus) => (
                    <button
                      type="button"
                      key={corpus.id}
                      className={corpora.includes(corpus.id) ? "on" : ""}
                      onClick={() => toggleCorpus(corpus.id)}
                      title={corpus.note}
                    >
                      {corpus.short}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </section>
        )}
      </header>

      <div className="workspace">
        <main className={`results-pane ${reading ? "reading" : ""}`}>
          {reading ? (
            <DocumentReader
              origin={reading}
              isInContext={(id) => isInContext(reading.corpus, id)}
              onToggle={(id, detail, pageNumber) => {
                if (isInContext(reading.corpus, id)) removeFromContext(reading.corpus, id);
                else clipPage(reading, id, detail, pageNumber);
              }}
              onClose={() => setReading(null)}
            />
          ) : (
            <>
              {!search && !searching && !searchError && (
                <section className="welcome">
                  <div className="welcome-eyebrow">Search · read · clip · send</div>
                  <h1>Build the context before the model sees it.</h1>
                  <p>
                    Every page is retrieved by BM25 over the source text itself — no embeddings, no
                    structured guesswork. Read the filings, clip the pages that matter, and only
                    those are sent.
                  </p>
                  {stats && (
                    <div className="stat-grid">
                      {stats.corpora.map((corpus) => (
                        <div key={corpus.corpus}>
                          <strong>{compactNumber(corpus.rows)}</strong>
                          <span>{CORPUS_LABELS[corpus.corpus] || corpus.corpus}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="example-list">
                    {EXAMPLES.map((example) => (
                      <button
                        type="button"
                        className="example"
                        key={example}
                        onClick={() => setQuery(example)}
                      >
                        {example}
                      </button>
                    ))}
                  </div>
                </section>
              )}

              {searchError && <div className="error-state">{searchError}</div>}

              {search && !searchError && (
                <>
                  <div className="result-count">
                    <span>
                      {search.result_count} pages · {search.parsed_terms.join(" · ")}
                    </span>
                    <span>
                      {search.match_mode === "all" ? "all terms" : "broadened to any term"}
                      {appliedSearch?.tickers.length ? " · balanced by ticker" : ""}
                    </span>
                  </div>

                  {resultGroups.length === 0 ? (
                    <div className="empty">
                      No pages match this scope. Broaden the period or corpus, or use fewer terms.
                    </div>
                  ) : (
                    resultGroups.map(([entity, sources]) => {
                      const isCollapsed = collapsed.has(entity);
                      // Pages reached by reading are in context but are not
                      // search results, so a "n/4 added" ratio would read 0
                      // while the panel shows several.
                      const inContextCount = stackedSources.filter(
                        (s) => s.entity === entity,
                      ).length;
                      const entityFlags = sources.flatMap((s) => s.quality_flags || []);
                      const severity = worstSeverity(entityFlags);

                      return (
                        <section className="entity-group" key={entity}>
                          <button
                            className="entity-head"
                            type="button"
                            onClick={() =>
                              setCollapsed((current) => {
                                const next = new Set(current);
                                if (next.has(entity)) next.delete(entity);
                                else next.add(entity);
                                return next;
                              })
                            }
                            aria-expanded={!isCollapsed}
                          >
                            <span className="entity-name">
                              {severity && (
                                <span
                                  className={`flag-dot ${severity === "blocker" ? "blocker" : ""}`}
                                  title={`${entityFlags.length} open deterministic checks`}
                                />
                              )}
                              {entity}
                            </span>
                            <span className="entity-meta">
                              {sources.length} result{sources.length === 1 ? "" : "s"}
                              {inContextCount > 0 && ` · ${inContextCount} clipped`}
                            </span>
                          </button>

                          {!isCollapsed &&
                            collapseRecurring(sources).map((family, index) => {
                              const members = familyMembers(family);
                              const familyKey = sourceKey(family.primary);
                              // Default to the best-scoring year; the picker
                              // below lets the researcher take another.
                              const source =
                                members.find((m) => m.id === chosenYear[familyKey]) ||
                                family.primary;
                              const key = sourceKey(source);
                              const added = isInContext(source.corpus, source.id);
                              const flags = source.quality_flags || [];
                              const flagSeverity = worstSeverity(flags);
                              const readable = source.corpus === "filing";

                              return (
                                <article className="page-row" key={key}>
                                  <div className="rank">{index + 1}</div>
                                  <div className="page-main">
                                    <div className="page-top">
                                      <h3 className="page-title">{source.title}</h3>
                                      <span className="page-tag">{source.period}</span>
                                    </div>

                                    {family.variants.length > 0 && (
                                      <div className="year-picker">
                                        <span>
                                          restated in {members.length} filings — showing
                                        </span>
                                        {members.map((member) => (
                                          <button
                                            type="button"
                                            key={member.id}
                                            className={[
                                              member.id === source.id ? "on" : "",
                                              isInContext(member.corpus, member.id)
                                                ? "clipped"
                                                : "",
                                            ]
                                              .filter(Boolean)
                                              .join(" ")}
                                            onClick={() =>
                                              setChosenYear((current) => ({
                                                ...current,
                                                [familyKey]: member.id,
                                              }))
                                            }
                                          >
                                            {member.period.slice(0, 4)}
                                            {isInContext(member.corpus, member.id) && (
                                              <i>✓</i>
                                            )}
                                          </button>
                                        ))}
                                      </div>
                                    )}

                                    <p className="page-summary">
                                      {source.summary || "This page has no indexed summary."}
                                    </p>

                                    {source.snippet && (
                                      <p className="page-snippet">
                                        <Excerpt text={source.snippet} />
                                      </p>
                                    )}

                                    <div className="badge-row">
                                      <span className="badge">
                                        {CORPUS_LABELS[source.corpus]}
                                        {source.page_number ? ` p.${source.page_number}` : ""}
                                      </span>
                                      <span className="badge" title="BM25 rank score for this query">
                                        #{index + 1} · {formatScore(source.bm25_score)}
                                      </span>
                                      {flags.length > 0 && (
                                        <span
                                          className={`badge ${flagSeverity === "blocker" ? "blocker" : "attention"}`}
                                          title={flags.map(flagLabel).join("\n")}
                                        >
                                          {flags.length} open check
                                          {flags.length === 1 ? "" : "s"}
                                        </span>
                                      )}
                                    </div>

                                    <div className="page-actions">
                                      {readable && (
                                        <button
                                          type="button"
                                          className="read-btn"
                                          onClick={() => setReading(source)}
                                        >
                                          open filing →
                                        </button>
                                      )}
                                      {!readable && source.source_url && (
                                        <a
                                          className="link-btn"
                                          href={source.source_url}
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          view source
                                        </a>
                                      )}
                                      <button
                                        type="button"
                                        className={`add-chip ${added ? "added" : ""}`}
                                        onClick={() => quickToggle(source)}
                                      >
                                        {added ? "✓ clipped" : "+ clip"}
                                      </button>
                                    </div>
                                  </div>
                                </article>
                              );
                            })}
                        </section>
                      );
                    })
                  )}
                </>
              )}
            </>
          )}
        </main>

        <aside className={`context-pane ${panelOpen ? "open" : ""}`} aria-label="Scrapbook">
          <div className="pane-head">
            <div className="pane-tabs">
              <button
                type="button"
                className={panelView === "context" ? "on" : ""}
                onClick={() => setPanelView("context")}
              >
                Scrapbook ({stackedSources.length})
              </button>
              <button
                type="button"
                className={panelView === "send" ? "on" : ""}
                disabled={!stackedSources.length}
                onClick={() => setPanelView("send")}
              >
                Send
              </button>
            </div>
            <button
              type="button"
              className="pane-close"
              onClick={() => setPanelOpen(false)}
              aria-label="Close panel"
            >
              ×
            </button>
          </div>

          <div className="pane-body">
            {panelView === "context" ? (
              stackedSources.length === 0 ? (
                <div className="empty">
                  Nothing clipped yet. Search, <b>open filing</b> on a result, and clip the pages
                  that belong. Your scrapbook is saved as you go.
                </div>
              ) : (
                <>
                  <div className="coverage-box">
                    {stackCoverage.map((line) => (
                      <div className="coverage-row" key={line.entity}>
                        <b>{line.entity}</b>
                        <span>
                          {line.pages} page{line.pages === 1 ? "" : "s"} ·{" "}
                          {line.periods.slice(0, 2).join(", ") || "no period"}
                          {line.periods.length > 2 ? " …" : ""}
                        </span>
                      </div>
                    ))}
                    <div className="coverage-total">~{compactNumber(tokenEstimate)} tokens</div>
                  </div>

                  <div className="refine-bar">
                    <span className="refine-label">Refine</span>
                    <button
                      type="button"
                      onClick={() => reshape(dedupeAdjacent(stackedSources), "Deduped")}
                    >
                      dedupe adjacent
                    </button>
                    <button
                      type="button"
                      onClick={() => reshape(latestFilingOnly(stackedSources), "Latest filing")}
                    >
                      latest filing
                    </button>
                    <button
                      type="button"
                      onClick={() => reshape(capPerEntity(stackedSources, 3), "Capped at 3")}
                    >
                      cap 3 each
                    </button>
                  </div>

                  {stackGroups.map(([entity, sources]) => (
                    <div key={entity}>
                      <div className="group-title">
                        <span>
                          {entity} — {sources.length}
                        </span>
                        <button type="button" onClick={() => removeGroup(entity)}>
                          remove all
                        </button>
                      </div>

                      {sources
                        .slice()
                        .sort((a, b) => (a.page_number || 0) - (b.page_number || 0))
                        .map((source) => {
                          const flags = source.detail?.quality_flags || source.quality_flags || [];
                          const chars = selectedContent(source).length;
                          return (
                            <div className="card" key={sourceKey(source)}>
                              <div className="card-head">
                                <div style={{ flex: 1, minWidth: 0 }}>
                                  <div className="card-title">
                                    {/* Two clips from different years of the
                                        same filing both read "Annual report —
                                        page 88" otherwise, which is genuinely
                                        confusing when reviewing a pack. */}
                                    {source.source_url ? (
                                      <a
                                        className="card-link"
                                        href={source.source_url}
                                        target="_blank"
                                        rel="noreferrer"
                                      >
                                        {source.entity} {source.period.slice(0, 4)} · p.
                                        {source.page_number ?? "?"} ↗
                                      </a>
                                    ) : (
                                      `${source.entity} ${source.period.slice(0, 4)} · p.${source.page_number ?? "?"}`
                                    )}
                                  </div>
                                  <div className="card-src">
                                    {source.period || "no period"} ·{" "}
                                    {source.loading
                                      ? "loading…"
                                      : source.detail?.asset.status || "not verified"}{" "}
                                    · ~{compactNumber(estimateTokens(chars))} tok
                                  </div>
                                </div>
                                <button
                                  type="button"
                                  className="remove-x"
                                  aria-label={`Remove ${source.title}`}
                                  onClick={() => removeFromContext(source.corpus, source.id)}
                                >
                                  ×
                                </button>
                              </div>

                              <div className="mode-toggle">
                                <button
                                  type="button"
                                  className={source.inclusionMode === "summary" ? "on" : ""}
                                  onClick={() => updateMode(source, "summary")}
                                >
                                  summary
                                </button>
                                <button
                                  type="button"
                                  disabled={source.loading || !source.detail?.text}
                                  className={source.inclusionMode === "full" ? "on" : ""}
                                  onClick={() => updateMode(source, "full")}
                                >
                                  full page
                                </button>
                              </div>

                              {flags.length > 0 && (
                                <ul className="flag-list">
                                  {flags.map((flag) => (
                                    <li key={flag.flag_id}>{flagLabel(flag)}</li>
                                  ))}
                                </ul>
                              )}
                            </div>
                          );
                        })}
                    </div>
                  ))}
                </>
              )
            ) : (
              <>
                <div className="summary-box">
                  <div className="summary-row">
                    <div className="k">Pages</div>
                    <div>{stackedSources.length}</div>
                  </div>
                  <div className="summary-row">
                    <div className="k">Entities</div>
                    <div>{stackGroups.length}</div>
                  </div>
                  <div className="summary-row">
                    <div className="k">Est. tokens</div>
                    <div>~{tokenEstimate.toLocaleString()}</div>
                  </div>
                  <div className="summary-row">
                    <div className="k">Est. input cost</div>
                    <div>{formatUsd(estimateCostUsd(activeProvider, tokenEstimate))}</div>
                  </div>
                </div>

                <div className="group-title">Provider</div>
                {PROVIDERS.map((entry) => {
                  const configured = configuredProviders.has(entry.id);
                  return (
                    <button
                      type="button"
                      key={entry.id}
                      className={`provider ${entry.id === provider ? "selected" : ""}`}
                      disabled={!configured}
                      onClick={() => setProvider(entry.id)}
                    >
                      <span>
                        <span className="provider-name">{entry.name}</span>
                        <span className="provider-meta">
                          {configured
                            ? `${entry.contextWindow} · ${entry.note}`
                            : `Not configured — set ${entry.keyVar}`}
                        </span>
                      </span>
                      <span className="provider-cost">
                        {formatUsd(estimateCostUsd(entry, tokenEstimate))}
                        <span>est.</span>
                      </span>
                    </button>
                  );
                })}

                <div className="prompt-label">Question for the analyst</div>
                <textarea
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  placeholder="What should the model work out from these sources?"
                />

                <button
                  className="btn"
                  type="button"
                  disabled={sending || !stackedSources.length || !prompt.trim()}
                  onClick={compileAnswer}
                >
                  {sending ? "Generating…" : "Generate"}
                </button>

                <div className="prompt-label">Take it with you</div>
                <div className="handoff">
                  <button type="button" onClick={() => copyText(markdown, "Markdown copied")}>
                    Copy Markdown
                  </button>
                  <button
                    type="button"
                    onClick={() => copyText(JSON.stringify(payload, null, 2), "JSON copied")}
                  >
                    Copy JSON
                  </button>
                </div>
                <div className="handoff">
                  <button
                    type="button"
                    onClick={() =>
                      downloadFile(`${slugify(sessionLabel)}.md`, markdown, "text/markdown")
                    }
                  >
                    ↓ Context .md
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      downloadFile(
                        `${slugify(sessionLabel)}.json`,
                        JSON.stringify(payload, null, 2),
                        "application/json",
                      )
                    }
                  >
                    ↓ Payload .json
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      downloadFile(
                        `${slugify(sessionLabel)}-sources.txt`,
                        buildSourceList(sessionLabel, stackedSources),
                        "text/plain",
                      )
                    }
                  >
                    ↓ Citations
                  </button>
                </div>

                {compileError && <div className="error-state">{compileError}</div>}

                {compiled && (
                  <div className="answer-block">
                    <div className="output-meta">
                      {compiled.model} · {stackedSources.length} pages
                      {compiled.usage?.total_tokens
                        ? ` · ${compiled.usage.total_tokens.toLocaleString()} tokens actual`
                        : ""}
                    </div>
                    <div className="output-box">{compiled.answer}</div>
                    <div className="handoff">
                      <button
                        type="button"
                        onClick={() => copyText(compiled.answer, "Answer copied")}
                      >
                        Copy answer
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </aside>
      </div>

      {/* Narrow screens only: the pane is off-canvas, so it needs a way back. */}
      <button
        type="button"
        className={`mobile-bar ${stackedSources.length > 0 && !panelOpen ? "show" : ""}`}
        onClick={() => setPanelOpen(true)}
      >
        <span className="left">
          <b>{stackedSources.length}</b> page{stackedSources.length === 1 ? "" : "s"} clipped
          <small>~{compactNumber(tokenEstimate)} tokens</small>
        </span>
        <span className="arrow">→</span>
      </button>

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
