/**
 * Markdown parsing for filing pages, kept separate from rendering.
 *
 * The pages are mostly financial tables; shown as raw pipe-delimited text they
 * are close to unreadable, which defeats the point of letting a researcher read
 * a page before clipping it.
 *
 * Parsing produces plain data, so it can be unit tested directly and so the
 * renderer only ever builds React elements from it — page content is OCR output
 * from third-party PDFs and must never be able to inject markup.
 *
 * This covers the subset the parser actually emits. Anything unrecognised falls
 * through as paragraph text rather than being dropped.
 */

export type Inline =
  | { kind: "text"; value: string }
  | { kind: "strong"; value: string }
  | { kind: "em"; value: string }
  | { kind: "code"; value: string };

export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "rule" }
  | { kind: "quote"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "table"; header: string[]; rows: string[][]; width: number };

const HEADING = /^(#{1,6})\s+(.*)$/;
const RULE = /^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/;
const UNORDERED = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const TABLE_ROW = /^\s*\|(.*)\|\s*$/;
const TABLE_DIVIDER = /^\s*\|[\s:|-]+\|\s*$/;
// The page-number comment the parser prepends; the reader shows a page header.
const PAGE_COMMENT = /^\s*<!--\s*Page\s+\d+\s*-->\s*$/i;

/** Splits a table row into cells, tolerating ragged trailing pipes. */
function cells(line: string): string[] {
  const match = line.match(TABLE_ROW);
  return (match ? match[1] : line).split("|").map((cell) => cell.trim());
}

/** True when a cell reads as a figure, so the renderer can right-align it. */
export function isNumericCell(value: string): boolean {
  // Compare after the same LaTeX unwrapping the renderer applies, so a cell
  // stored as `$51.60 \%$` is still recognised as a figure.
  const cleaned = value
    .replace(/\$([^$\n]{1,60})\$/g, "$1")
    .replace(/\\([%$&])/g, "$1")
    .trim();
  return /^[(\-]?[\d,.]+\)?\s*%?$/.test(cleaned) && /\d/.test(cleaned);
}

/**
 * Tokenises emphasis, inline code, and backslash escapes.
 *
 * Ordered longest-delimiter-first so `**bold**` is not consumed by the
 * single-asterisk italic rule.
 */
export function parseInline(text: string): Inline[] {
  const nodes: Inline[] = [];
  const source = text
    // The OCR emits LaTeX inline-math wrappers around figures, so a cell reads
    // `$51.60 \%$` where the filing says `51.60%`. Unwrap before unescaping,
    // and require both delimiters so a plain `$51.60` is untouched.
    .replace(/\$([^$\n]{1,60})\$/g, "$1")
    // Escaped punctuation such as `\&`, `\%` and `\_` is common in this output.
    .replace(/\\([\\`*_{}[\]()#+\-.!&%$])/g, "$1")
    .trim();
  const pattern = /(\*\*|__)(.+?)\1|(\*|_)(?!\s)(.+?)(?<!\s)\3|`([^`]+)`/g;

  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(source)) !== null) {
    if (match.index > lastIndex) {
      nodes.push({ kind: "text", value: source.slice(lastIndex, match.index) });
    }
    if (match[2] !== undefined) nodes.push({ kind: "strong", value: match[2] });
    else if (match[4] !== undefined) nodes.push({ kind: "em", value: match[4] });
    else if (match[5] !== undefined) nodes.push({ kind: "code", value: match[5] });
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < source.length) {
    nodes.push({ kind: "text", value: source.slice(lastIndex) });
  }
  return nodes;
}

export function parseBlocks(markdown: string): Block[] {
  const lines = (markdown || "").split(/\r?\n/);
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length === 0) return;
    const text = paragraph.join(" ").trim();
    paragraph = [];
    if (text) blocks.push({ kind: "paragraph", text });
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];

    if (PAGE_COMMENT.test(line)) continue;

    if (!line.trim()) {
      flush();
      continue;
    }

    const heading = line.match(HEADING);
    if (heading) {
      flush();
      blocks.push({
        kind: "heading",
        level: Math.min(heading[1].length, 6),
        text: heading[2],
      });
      continue;
    }

    if (RULE.test(line)) {
      flush();
      blocks.push({ kind: "rule" });
      continue;
    }

    // A table is a pipe row followed by a divider row. Without the divider it is
    // just a line that happens to contain pipes, so it stays a paragraph.
    if (TABLE_ROW.test(line) && index + 1 < lines.length && TABLE_DIVIDER.test(lines[index + 1])) {
      flush();
      const header = cells(line);
      const rows: string[][] = [];
      let cursor = index + 2;
      while (cursor < lines.length && TABLE_ROW.test(lines[cursor])) {
        rows.push(cells(lines[cursor]));
        cursor += 1;
      }
      index = cursor - 1;
      const width = Math.max(header.length, ...rows.map((row) => row.length), 1);
      blocks.push({ kind: "table", header, rows, width });
      continue;
    }

    const quote = line.match(QUOTE);
    if (quote) {
      flush();
      blocks.push({ kind: "quote", text: quote[1] });
      continue;
    }

    if (UNORDERED.test(line) || ORDERED.test(line)) {
      flush();
      const ordered = ORDERED.test(line) && !UNORDERED.test(line);
      const items: string[] = [];
      let cursor = index;
      while (cursor < lines.length) {
        const unorderedMatch = lines[cursor].match(UNORDERED);
        const orderedMatch = lines[cursor].match(ORDERED);
        if (ordered && orderedMatch) items.push(orderedMatch[2]);
        else if (!ordered && unorderedMatch) items.push(unorderedMatch[1]);
        else break;
        cursor += 1;
      }
      index = cursor - 1;
      blocks.push({ kind: "list", ordered, items });
      continue;
    }

    paragraph.push(line.trim());
  }

  flush();
  return blocks;
}
