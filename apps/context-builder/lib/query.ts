import type { CorpusKind } from "./corpus";
import type { AppliedSearch } from "./context";

/**
 * Deterministic query interpretation.
 *
 * There is no model in this path. A typed question is reduced to BM25 terms
 * plus explicit scope by fixed rules, and the result is shown back to the
 * researcher so the retrieval stays inspectable.
 */

export type PeriodPreset = "all" | "3y" | "5y" | "custom";

/** Dropped before the FTS query: they match nearly every page and add no signal. */
const STOPWORDS = /\b(of|for|and|the|from|between|in|on|at|to|a|an|is|are|was|were|with|by|show|me|what|which)\b/gi;

const FILING_INTENT =
  /\b(financial statements?|annual reports?|quarterly reports?|financial reports?|balance sheets?|accounts?|income statements?|cash flows?)\b/i;

const ANNOUNCEMENT_INTENT =
  /\b(announcements?|notices?|disclosures?|board meetings?|dividends?|corporate actions?)\b/i;

export function resolveDates(
  preset: PeriodPreset,
  fromYear: string,
  toYear: string,
  currentYear: number,
): { dateFrom: string | null; dateTo: string | null } {
  if (preset === "all") return { dateFrom: null, dateTo: null };
  if (preset === "3y") {
    return { dateFrom: `${currentYear - 2}-01-01`, dateTo: `${currentYear}-12-31` };
  }
  if (preset === "5y") {
    return { dateFrom: `${currentYear - 4}-01-01`, dateTo: `${currentYear}-12-31` };
  }
  const from = Number(fromYear);
  const to = Number(toYear);
  return {
    dateFrom: Number.isFinite(from) && from > 1900 ? `${from}-01-01` : null,
    dateTo: Number.isFinite(to) && to > 1900 ? `${to}-12-31` : null,
  };
}

export interface InferOptions {
  rawQuery: string;
  selectedTickers: string[];
  knownTickers: Set<string>;
  periodPreset: PeriodPreset;
  fromYear: string;
  toYear: string;
  selectedCorpora: CorpusKind[];
  currentYear: number;
}

export function inferSearch(options: InferOptions): AppliedSearch {
  const {
    rawQuery,
    selectedTickers,
    knownTickers,
    periodPreset,
    fromYear,
    toYear,
    selectedCorpora,
    currentYear,
  } = options;

  // Only uppercase tokens that are real tickers are treated as scope. This
  // avoids turning an ordinary capitalized word into a silent filter.
  const inferredTickers = [...rawQuery.matchAll(/\b[A-Z][A-Z0-9.-]{1,15}\b/g)]
    .map((match) => match[0].toUpperCase())
    .filter((token) => knownTickers.has(token));
  const tickers = [...new Set([...selectedTickers, ...inferredTickers])];

  let dates = resolveDates(periodPreset, fromYear, toYear, currentYear);
  const yearsMatch = rawQuery.match(/\blast\s+(\d{1,2})\s+years?\b/i);
  if (periodPreset === "all" && yearsMatch) {
    const years = Math.max(1, Math.min(Number(yearsMatch[1]), 20));
    dates = {
      dateFrom: `${currentYear - years + 1}-01-01`,
      dateTo: `${currentYear}-12-31`,
    };
  }

  // Intent narrowing applies only when the researcher has not already narrowed
  // the corpus. An explicit choice is never overridden.
  const untouched = selectedCorpora.length === 3;
  let corpora = selectedCorpora;
  if (untouched && FILING_INTENT.test(rawQuery)) corpora = ["filing"];
  else if (untouched && ANNOUNCEMENT_INTENT.test(rawQuery)) corpora = ["announcement"];

  let lexicalQuery = rawQuery;
  for (const ticker of tickers) {
    const escaped = ticker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    lexicalQuery = lexicalQuery.replace(new RegExp(`\\b${escaped}\\b`, "gi"), " ");
  }
  lexicalQuery = lexicalQuery
    .replace(/\blast\s+\d{1,2}\s+years?\b/gi, " ")
    .replace(/[,&/?]+/g, " ")
    .replace(STOPWORDS, " ")
    .replace(/\s+/g, " ")
    .trim();

  // Stripping scope can empty the query. The previous fallback substituted the
  // literal phrase "financial statements", which is the single worst query in
  // this corpus: contents pages are short and repeat every statement name, so
  // BM25 ranks them above every real page. Fall back to the researcher's own
  // words instead, and let `adviseQuery` warn about what they will get.
  if (!lexicalQuery) lexicalQuery = rawQuery.trim();

  return {
    naturalQuery: rawQuery.trim(),
    lexicalQuery,
    tickers,
    dateFrom: dates.dateFrom,
    dateTo: dates.dateTo,
    corpora,
  };
}

export function describeScope(search: AppliedSearch, labels: Record<string, string>): string {
  return [
    search.tickers.length ? search.tickers.join(", ") : "all entities",
    search.dateFrom && search.dateTo
      ? `${search.dateFrom.slice(0, 4)}–${search.dateTo.slice(0, 4)}`
      : "all periods",
    search.corpora.map((corpus) => labels[corpus] || corpus).join(", "),
  ].join(" · ");
}
