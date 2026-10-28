import type { StackedSource } from "./corpus";
import type { Scrapbook } from "./scrapbook";

/**
 * Getting evidence out of the tool and into the analyst's own work.
 *
 * A scrapbook is a workpaper, and a workpaper has to leave the app: into a
 * memo, into a model, into a reviewer's hands, and into next quarter's update.
 * Clipboard-only handoff loses to a file the moment anyone needs to attach it
 * to something.
 *
 * Every export carries the source URL for each page, because the question that
 * follows any number in a memo is "where did that come from?"
 */

export function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "scrapbook"
  );
}

export function downloadFile(filename: string, contents: string, mime: string): void {
  const blob = new Blob([contents], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking immediately can cancel the download in some browsers.
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/**
 * A citation list for a memo footnote or an appendix.
 *
 * Ordered to match the source numbers used in the analyst context, so [S3] in
 * a compiled answer lines up with entry 3 here.
 */
export function buildSourceList(sessionLabel: string, sources: StackedSource[]): string {
  const lines = sources.map((source, index) => {
    const parts = [
      `${index + 1}. ${source.entity} — ${source.title}`,
      source.period ? `(${source.period})` : "",
    ]
      .filter(Boolean)
      .join(" ");
    const url = source.source_url || source.detail?.source_url;
    return url ? `${parts}\n   ${url}` : parts;
  });

  return [
    `Sources — ${sessionLabel || "Analyst context"}`,
    `Retrieved ${new Date().toISOString().slice(0, 10)} from the psxGPT authoritative corpus.`,
    "",
    ...lines,
  ].join("\n");
}

/** The portable form of a scrapbook, for moving between browsers or machines. */
export interface ScrapbookFile {
  format: "ledger.scrapbook";
  version: 1;
  exportedAt: string;
  scrapbooks: Scrapbook[];
}

export function buildScrapbookFile(scrapbooks: Scrapbook[]): string {
  const file: ScrapbookFile = {
    format: "ledger.scrapbook",
    version: 1,
    exportedAt: new Date().toISOString(),
    scrapbooks,
  };
  return JSON.stringify(file, null, 2);
}

/**
 * Reads an exported file back.
 *
 * Returns the scrapbooks it recognises rather than throwing, so one malformed
 * entry cannot cost the researcher the rest of the import.
 */
export function parseScrapbookFile(contents: string): Scrapbook[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(contents);
  } catch {
    return [];
  }
  const file = parsed as Partial<ScrapbookFile>;
  if (file?.format !== "ledger.scrapbook" || !Array.isArray(file.scrapbooks)) return [];
  return file.scrapbooks.filter(
    (book): book is Scrapbook =>
      Boolean(book) && typeof book.id === "string" && Array.isArray(book.pages),
  );
}

/** Merges imported scrapbooks, re-identifying any that collide with existing ones. */
export function mergeScrapbooks(existing: Scrapbook[], incoming: Scrapbook[]): Scrapbook[] {
  const taken = new Set(existing.map((book) => book.id));
  const added = incoming.map((book) => {
    if (!taken.has(book.id)) {
      taken.add(book.id);
      return book;
    }
    // A re-import of the same file must not overwrite work done since.
    const id = `${book.id}_${Math.random().toString(36).slice(2, 8)}`;
    taken.add(id);
    return { ...book, id, name: `${book.name} (imported)` };
  });
  return [...added, ...existing];
}
