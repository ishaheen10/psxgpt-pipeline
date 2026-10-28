/**
 * Retrieval vocabulary for the filing corpus.
 *
 * Ported verbatim in substance from the serving engine's lane pack
 * (`psxgpt-serve/apps/analysis/context/lane/fact/financial_documents_new.md`,
 * section SEARCH_TEXT VOCABULARY INVENTORY). That pack exists because BM25 over
 * this corpus rewards terms describing what a page *discusses* and punishes
 * terms describing which *section* it belongs to.
 *
 * In psxgpt-serve this inventory is given to the lane model. Here the human is
 * the one writing the query, so the same knowledge has to be visible in the UI
 * rather than buried in a prompt.
 *
 * `search_text` is an 18–35 word summary generated specifically for BM25 and
 * front-loaded with high-signal concepts, so these terms are chosen to match
 * what actually lives in that field.
 */

export interface TermGroup {
  id: string;
  label: string;
  /** Shown when the group is opened, to explain when it is the right group. */
  hint: string;
  terms: string[];
  /** Sector-specific groups are listed after the general ones. */
  sector?: boolean;
}

export const TERM_GROUPS: TermGroup[] = [
  {
    id: "narrative",
    label: "Outlook & strategy",
    hint: "Management's own commentary on where the business is going",
    terms: [
      "future outlook",
      "near-term outlook",
      "chairman review",
      "capital allocation",
      "diversification",
      "expansion plan",
      "strategic",
      "going forward",
    ],
  },
  {
    id: "rate_margin",
    label: "Rates & margins",
    hint: "Profitability drivers and rate sensitivity",
    terms: [
      "policy rate",
      "cost of funds",
      "cost to income",
      "NIM",
      "return on equity",
      "earnings per share",
    ],
  },
  {
    id: "deposits",
    label: "Deposits & funding",
    hint: "Bank funding mix. Prefer these over the word 'deposits' alone",
    terms: [
      "deposit composition",
      "deposits and other accounts",
      "current deposits",
      "savings deposits",
      "term deposits",
      "customer-wise deposits",
      "CASA",
      "low-cost deposits",
      "cost of deposits",
      "home remittances",
      "funding strategy",
    ],
  },
  {
    id: "lending",
    label: "Lending & credit",
    hint: "Loan book composition and asset quality",
    terms: [
      "loan portfolio",
      "gross advances",
      "sectoral concentration",
      "advances breakdown",
      "credit mix",
      "NPL coverage",
      "infection ratio",
    ],
  },
  {
    id: "risk",
    label: "Risk disclosures",
    hint: "Use these instead of the bare word 'risk', which matches training and ESG pages",
    terms: [
      "key risk",
      "principal risk",
      "material risk",
      "ERM",
      "PESTEL",
      "business risk",
    ],
  },
  {
    id: "returns",
    label: "Dividends & returns",
    hint: "Distribution policy and per-share economics",
    terms: [
      "dividend policy",
      "dividend per share",
      "earnings per share",
      "share buyback",
      "payout ratio",
    ],
  },
  {
    id: "energy",
    label: "Energy & power",
    hint: "Independent power producers, generation and tariffs",
    sector: true,
    terms: [
      "TPPA",
      "PPA",
      "circular debt",
      "renewable energy",
      "capacity utilization",
      "generation license",
      "merit order",
    ],
  },
  {
    id: "fertiliser",
    label: "Fertiliser",
    hint: "Urea and DAP volumes, pricing and offtake",
    sector: true,
    terms: [
      "urea demand",
      "urea sales",
      "market share",
      "offtake",
      "domestic demand",
      "export parity",
    ],
  },
  {
    id: "cement",
    label: "Cement",
    hint: "Dispatches, clinker and utilisation",
    sector: true,
    terms: [
      "cement demand",
      "clinker",
      "capacity utilization",
      "export growth",
      "domestic demand subdued",
    ],
  },
  {
    id: "textile",
    label: "Textile",
    hint: "Export-led demand",
    sector: true,
    terms: [
      "export sales",
      "export growth",
      "export demand",
      "textile exports",
      "export market",
      "export momentum",
    ],
  },
  {
    id: "oil_gas",
    label: "Oil, gas & refining",
    hint: "Marketing and refining, including receivable pressure",
    sector: true,
    terms: [
      "circular debt",
      "trade debts",
      "working capital",
      "receivables",
      "credit lines",
      "import parity",
      "refinery margins",
    ],
  },
];

/**
 * Terms that match boilerplate across every filing.
 *
 * Each entry records what it actually pulls back, because the useful message to
 * a researcher is not "this is a bad term" but "this will return contents and
 * cover pages".
 */
export const LOW_SIGNAL: Array<{ term: string; matches: string; instead?: string[] }> = [
  {
    term: "directors report",
    matches: "contents, cover and cross-reference pages",
    instead: ["future outlook", "chairman review"],
  },
  {
    term: "management discussion",
    matches: "financial-note section headers",
    instead: ["future outlook", "business risk"],
  },
  {
    term: "going concern",
    matches: "auditor opinion paragraphs",
    instead: ["principal risk", "liquidity"],
  },
  {
    term: "notes to financial statements",
    matches: "accounting note headers, not the note content",
  },
  {
    term: "financial statements",
    matches: "contents pages, which list every statement by name",
  },
  {
    term: "annual report",
    matches: "cover and contents pages",
  },
  {
    term: "balance sheet",
    matches: "contents pages and note headers",
  },
  {
    term: "risk",
    matches: "employee training pages, ESG disclosures and note headers",
    instead: ["key risk", "principal risk", "material risk"],
  },
  {
    term: "outlook",
    matches: "little on its own — the summaries use the longer phrase",
    instead: ["future outlook", "near-term outlook"],
  },
  {
    term: "performance",
    matches: "cover pages and section dividers",
  },
  {
    term: "results",
    matches: "contents pages and notice-of-meeting pages",
  },
  {
    term: "report",
    matches: "almost every page of every filing",
  },
];

const LOW_SIGNAL_INDEX = new Map(LOW_SIGNAL.map((entry) => [entry.term, entry]));

/** All high-signal terms, for detecting whether a query already has one. */
const HIGH_SIGNAL_TERMS = new Set(
  TERM_GROUPS.flatMap((group) => group.terms.map((term) => term.toLowerCase())),
);

export interface QueryAdvice {
  /** Low-signal phrases present in the query, longest match first. */
  weak: Array<{ term: string; matches: string; instead?: string[] }>;
  /** True when nothing in the query looks like a high-signal concept. */
  noStrongTerm: boolean;
  suggestions: string[];
}

/**
 * Inspects a query before it is run.
 *
 * This deliberately advises rather than rewrites. The premise of the app is
 * that the human owns retrieval, so a query that looks weak is still run — the
 * researcher is simply told what it will pull back and offered better terms.
 */
export function adviseQuery(query: string): QueryAdvice {
  const lowered = ` ${query.toLowerCase().replace(/[^a-z0-9\s-]/g, " ").replace(/\s+/g, " ")} `;

  const weak = LOW_SIGNAL.filter((entry) => lowered.includes(` ${entry.term} `))
    // Longest first, so "financial statements" is reported ahead of a bare word.
    .sort((a, b) => b.term.length - a.term.length);

  // A phrase already covered by a longer weak phrase is not reported twice.
  const deduped: typeof weak = [];
  for (const entry of weak) {
    if (!deduped.some((kept) => kept.term.includes(entry.term))) deduped.push(entry);
  }

  const hasStrong = [...HIGH_SIGNAL_TERMS].some((term) => lowered.includes(term.toLowerCase()));

  const suggestions = deduped.flatMap((entry) => entry.instead || []).slice(0, 4);

  return {
    weak: deduped,
    noStrongTerm: !hasStrong && deduped.length > 0,
    suggestions,
  };
}

export function isLowSignal(term: string): boolean {
  return LOW_SIGNAL_INDEX.has(term.toLowerCase());
}
