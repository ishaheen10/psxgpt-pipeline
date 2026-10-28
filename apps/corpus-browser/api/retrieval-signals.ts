export interface PageSignals {
  title: string;
  headings: string[];
  tableSignature: string[];
  tableRows: number;
  numericDensity: number;
  navigationLikelihood: number;
}

const GENERIC_HEADINGS = [
  /\bnotes? to (?:and forming part of )?(?:the )?(?:consolidated |unconsolidated )?financial statements?\b/i,
  /\bfor the (?:year|period|quarter|six months|nine months) ended\b/i,
  /\bannual report\b/i,
  /\bquarterly report\b/i,
  /\btable of contents\b/i,
  /^contents?$/i,
  /^page \d+$/i,
];

const STOP_WORDS = new Set([
  "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
  "in", "is", "it", "of", "on", "or", "page", "report", "the", "this", "to", "was",
  "were", "with", "year", "years",
]);

function cleanCue(value: string): string {
  return value
    .trim()
    .replace(/^#{1,6}\s*/, "")
    .replace(/^[-*]\s+/, "")
    .replace(/^\*\*(.*?)\*\*$/, "$1")
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[|:;,.]+$/, "")
    .trim()
    .replace(/^\*\*(.*?)\*\*$/, "$1")
    .trim();
}

function isGenericHeading(value: string): boolean {
  return GENERIC_HEADINGS.some((pattern) => pattern.test(value));
}

function isTableDivider(line: string): boolean {
  return /^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$/.test(line);
}

function looksLikeEvidenceCue(value: string): boolean {
  const words = value.match(/[A-Za-z][A-Za-z0-9&/()-]*/g) || [];
  if (words.length < 2 || words.length > 18 || value.length > 150) return false;
  if (/^<!--/.test(value) || /^\|/.test(value)) return false;
  if (/^(?:rupees?|rs\.?|pkr)\b/i.test(value)) return false;
  return true;
}

function cueScore(cue: string, kind: "markdown" | "bold" | "uppercase" | "table-adjacent"): number {
  if (!looksLikeEvidenceCue(cue) || isGenericHeading(cue)) return -100;
  let score = kind === "table-adjacent" ? 9 : kind === "markdown" ? 7 : kind === "bold" ? 6 : 5;
  if (/^\d+(?:\.\d+)*\s+/.test(cue)) score += 3;
  if (/\b(note|statement|ratio|adequacy|risk|exposure|sector|segment|revenue|profit|loss|cash flow|dividend|inflation|growth)\b/i.test(cue)) {
    score += 3;
  }
  if (cue.length >= 18 && cue.length <= 90) score += 2;
  return score;
}

function tableCells(line: string): string[] {
  return line
    .split("|")
    .map(cleanCue)
    .filter(Boolean)
    .filter((cell) => !/^[-:]+$/.test(cell))
    .filter((cell) => !/^(?:19|20)\d{2}$/.test(cell))
    .filter((cell) => !/^[\d,().%/'-]+$/.test(cell));
}

/**
 * Derive useful retrieval keys from markdown that already exists in D1.
 * Nothing here depends on section_tags.
 */
export function extractPageSignals(markdown: unknown): PageSignals {
  const text = String(markdown || "").slice(0, 12_000);
  const lines = text.split(/\r?\n/);
  const cues: Array<{ cue: string; score: number; order: number }> = [];
  const tableSignature: string[] = [];
  let tableRows = 0;

  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index].trim();
    if (!raw) continue;

    let kind: "markdown" | "bold" | "uppercase" | null = null;
    if (/^#{1,6}\s+\S/.test(raw)) kind = "markdown";
    else if (/^\*\*[^*]{4,160}\*\*$/.test(raw)) kind = "bold";
    else if (
      raw.length >= 8
      && raw.length <= 150
      && /[A-Z]/.test(raw)
      && raw === raw.toUpperCase()
      && !/[|]/.test(raw)
    ) kind = "uppercase";
    if (kind) {
      const cue = cleanCue(raw);
      cues.push({ cue, score: cueScore(cue, kind), order: index });
    }

    if (raw.startsWith("|") && !isTableDivider(raw)) {
      tableRows += 1;
      let previous = "";
      for (let lookback = index - 1; lookback >= Math.max(0, index - 4); lookback -= 1) {
        previous = cleanCue(lines[lookback] || "");
        if (previous) break;
      }
      if (previous && !previous.startsWith("|") && looksLikeEvidenceCue(previous)) {
        cues.push({
          cue: previous,
          score: cueScore(previous, "table-adjacent"),
          order: index - 1,
        });
      }
      if (tableRows <= 4) {
        const cells = tableCells(raw);
        for (const cell of cells) {
          if (!tableSignature.some((existing) => existing.toLowerCase() === cell.toLowerCase())) {
            tableSignature.push(cell);
          }
          if (
            looksLikeEvidenceCue(cell)
            && (
              /\b(credit risk .*(?:sector|exposure)|capital adequacy|leverage ratio|liquidity requirements?|operating expenses?|production capacity|capacity utilization|deposits? and other accounts|statement of|profit and loss|cash flow|segment (?:revenue|results?))\b/i.test(cell)
              || cell === cell.toUpperCase()
            )
          ) {
            cues.push({
              cue: cell,
              score: cueScore(cell, "table-adjacent") + 1,
              order: index,
            });
          }
          if (tableSignature.length >= 8) break;
        }
      }
    }
  }

  const headings = [...new Map(
    cues
      .filter((item) => item.score > -100)
      .sort((left, right) => right.score - left.score || left.order - right.order)
      .map((item) => [item.cue.toLowerCase(), item.cue]),
  ).values()].slice(0, 8);
  const title = headings[0] || "Filing page";
  const words = text.match(/\S+/g) || [];
  const numericWords = words.filter((word) => /\d/.test(word)).length;
  const navigationMarkers = (text.match(/\.{4,}|table of contents|^contents?$/gim) || []).length;

  return {
    title,
    headings,
    tableSignature: tableSignature.slice(0, 8),
    tableRows,
    numericDensity: words.length ? numericWords / words.length : 0,
    navigationLikelihood: Math.min(1, navigationMarkers / 3),
  };
}

/**
 * Pick the local section around the strongest query match. A page can contain
 * several notes, so its first heading is often less useful than the heading
 * nearest the matched table row.
 */
export function deriveMatchedHeading(markdown: unknown, query: unknown): string {
  const text = String(markdown || "").slice(0, 16_000);
  const lines = text.split(/\r?\n/);
  const tokens = [...new Set(meaningfulTokens(String(query || "")))];
  if (!tokens.length) return "";

  let bestIndex = -1;
  let bestCoverage = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const normalized = ` ${lexicalTokens(lines[index], 80).join(" ")} `;
    const coverage = tokens.filter((token) => normalized.includes(` ${token} `)).length / tokens.length;
    if (coverage > bestCoverage) {
      bestCoverage = coverage;
      bestIndex = index;
    }
  }
  if (bestIndex < 0 || bestCoverage < 0.25) return "";

  const matchedLine = cleanCue(lines[bestIndex]);
  if (bestCoverage >= 0.75) {
    const cells = matchedLine.startsWith("|") ? tableCells(matchedLine) : [];
    const matchedCell = cells.find((cell) => tokenCoverageForCue(tokens, cell) >= 0.75);
    if (matchedCell) return matchedCell;
    if (looksLikeEvidenceCue(matchedLine) && !isGenericHeading(matchedLine)) return matchedLine;
  }

  for (let index = bestIndex; index >= Math.max(0, bestIndex - 32); index -= 1) {
    const raw = lines[index].trim();
    const cue = cleanCue(raw);
    if (!cue || isGenericHeading(cue)) continue;
    const headingLike = /^#{1,6}\s+\S/.test(raw)
      || /^\*\*[^*]{4,160}\*\*$/.test(raw)
      || /^\d+(?:\.\d+)*\s+[A-Za-z]/.test(cue)
      || (cue.length <= 150 && cue === cue.toUpperCase() && /[A-Z]/.test(cue));
    if (headingLike && looksLikeEvidenceCue(cue)) return cue;
  }
  return "";
}

/**
 * Choose the most query-specific title from independently derived page cues.
 * This keeps a broad nearby section heading from hiding a more precise note or
 * table heading that is already present in the page signals.
 */
export function selectEvidenceTitle(candidates: unknown[], query: unknown): string {
  const queryTokens = [...new Set(meaningfulTokens(String(query || "")))];
  const uniqueCandidates = [...new Map(
    candidates
      .map((candidate) => cleanCue(String(candidate || "")))
      .filter(Boolean)
      .map((candidate) => [candidate.toLowerCase(), candidate]),
  ).values()];
  if (!uniqueCandidates.length) return "";
  if (!queryTokens.length) return uniqueCandidates[0];

  return uniqueCandidates
    .map((candidate, index) => {
      const coverage = tokenCoverageForCue(queryTokens, candidate);
      const score = coverage * 100
        + (isGenericHeading(candidate) ? -20 : 0)
        + (looksLikeEvidenceCue(candidate) ? 5 : 0);
      return { candidate, score, coverage, index };
    })
    .sort((left, right) =>
      right.score - left.score
      || right.coverage - left.coverage
      || left.index - right.index
    )[0].candidate;
}

export function buildTableEvidenceTitle(
  tableSignature: unknown[],
  query: unknown,
): string {
  const queryTokens = [...new Set(meaningfulTokens(String(query || "")))];
  if (!queryTokens.length) return "";
  const ranked = tableSignature
    .map((value, index) => {
      const candidate = cleanCue(String(value || ""));
      return {
        candidate,
        index,
        coverage: tokenCoverageForCue(queryTokens, candidate),
      };
    })
    .filter((item) => item.candidate && item.coverage > 0)
    .sort((left, right) =>
      right.coverage - left.coverage
      || left.index - right.index
    )
    .slice(0, 3)
    .sort((left, right) => left.index - right.index);
  return ranked.map((item) => item.candidate).join(" · ").slice(0, 150);
}

/**
 * Produce a useful card title from the existing compact search_text when the
 * page Markdown has no recoverable heading. Search summaries are deliberately
 * comma-delimited discovery keys, so select the short clause that best matches
 * the active query instead of showing "Annual report — page N".
 */
export function deriveSummaryTitle(searchText: unknown, query: unknown): string {
  const source = String(searchText || "").replace(/\s+/g, " ").trim();
  const queryTokens = [...new Set(meaningfulTokens(String(query || "")))];
  if (!source || !queryTokens.length) return "";

  const clauses = source
    .split(/\s*[,;]\s*/)
    .map((value) => cleanCue(value)
      .replace(/^statement\s+/i, "")
      .replace(/\s+note(?:\s+\d+(?:\.\d+)*)?$/i, "")
      .replace(/\s+(?:PKR|Rs\.?|rupees?)\s+.*$/i, "")
      .replace(/\s+\d[\d.,%]*\s+(?:billion|million|thousand)(?:\s+rupees?)?$/i, "")
      .trim())
    .filter((value) => {
      const words = value.match(/[A-Za-z][A-Za-z0-9&/()-]*/g) || [];
      return value.length >= 7 && value.length <= 90 && words.length >= 2 && words.length <= 12;
    });
  if (!clauses.length) return "";

  const evidenceCue = /\b(capacity|cost|currency risk|deposit|disaggregation|exposure|fuel|income|marketing|production|revenue|risk|segment)\b/i;
  const generic = /^(?:annual report|financial statements?|note(?: \d+(?:\.\d+)*)?)$/i;
  const ranked = clauses
    .map((clause, index) => {
      const coverage = tokenCoverageForCue(queryTokens, clause);
      const score = coverage * 10
        + (evidenceCue.test(clause) ? 2 : 0)
        + Math.max(0, 2.5 - index * 0.25)
        + (/\d/.test(clause) ? 0 : 0.5)
        - (generic.test(clause) ? 10 : 0);
      return { clause, score, coverage, index };
    })
    .sort((left, right) =>
      right.score - left.score
      || right.coverage - left.coverage
      || left.index - right.index,
    );
  const selected = ranked[0];
  if (!selected || selected.score < 3) return "";
  return selected.clause.charAt(0).toUpperCase() + selected.clause.slice(1);
}

function tokenCoverageForCue(tokens: string[], value: string): number {
  const normalized = ` ${lexicalTokens(value, 80).join(" ")} `;
  return tokens.length
    ? tokens.filter((token) => normalized.includes(` ${token} `)).length / tokens.length
    : 0;
}

export function lexicalTokens(value: unknown, limit = 16): string[] {
  return (String(value || "").normalize("NFKC").match(/[\p{L}\p{N}][\p{L}\p{N}._/-]*/gu) || [])
    .map((token) => token.replace(/"/g, "").trim().toLowerCase())
    .filter(Boolean)
    .slice(0, limit);
}

export function buildPhraseQuery(value: unknown): string {
  const tokens = lexicalTokens(value, 10);
  return tokens.length >= 2 ? `"${tokens.join(" ")}"` : "";
}

export function meaningfulTokens(value: string): string[] {
  return lexicalTokens(value, 32).filter((token) => token.length >= 3 && !STOP_WORDS.has(token));
}

/**
 * Turn a selected page into a compact query prototype for "find similar".
 * Prefer discriminative headings and table labels; use summary terms only to
 * fill gaps. This keeps the original page as the Value while searching its Key.
 */
export function buildSimilarPrototype(
  markdown: unknown,
  searchText: unknown,
  activeQuery: unknown,
): { query: string; heading: string; summary: string; text: string; signals: PageSignals } {
  const signals = extractPageSignals(markdown);
  const specificHeading = signals.headings[0] || "";
  const activeTokens = meaningfulTokens(String(activeQuery || ""));
  const headingTokens = meaningfulTokens(specificHeading);
  const tableTokens = signals.tableSignature.flatMap(meaningfulTokens);
  const summaryTokens = meaningfulTokens(String(searchText || ""));
  const ordered = [...activeTokens, ...headingTokens, ...tableTokens, ...summaryTokens];
  const unique = [...new Set(ordered)].slice(0, 10);
  const query = unique.join(" ") || "authoritative evidence";
  return {
    query,
    heading: specificHeading || query,
    summary: [...new Set([...headingTokens, ...tableTokens, ...summaryTokens])].slice(0, 10).join(" ") || query,
    text: [...new Set([...headingTokens, ...tableTokens])].slice(0, 10).join(" ") || query,
    signals,
  };
}
