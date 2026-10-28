#!/usr/bin/env node

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createWriteStream } from "node:fs";
import { mkdir, opendir, readdir, readFile, unlink, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = resolve(appRoot, "../..");
const sourceRoot = resolve(projectRoot, "markdown_pages");
const outputRoot = resolve(projectRoot, "artifacts/corpus_browser_structure_index");
const rowsPath = join(outputRoot, "page_structures.jsonl");
const initSqlPath = join(outputRoot, "000-init.sql");
const finalizeSqlPath = join(outputRoot, "999-finalize.sql");
const reportPath = join(outputRoot, "build_report.json");
const catalogPath = join(outputRoot, "filing_catalog.jsonl");
const databaseName = process.env.CORPUS_SOURCE_D1 || "psx";
const concurrency = Math.max(1, Number(process.env.STRUCTURE_INDEX_CONCURRENCY) || 48);
const rowsPerSqlFile = Math.max(100, Number(process.env.STRUCTURE_INDEX_SQL_ROWS) || 2_000);

function cleanInlineMarkdown(value) {
  return value
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/!\[[^\]]*]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)]\([^)]*\)/g, "$1")
    .replace(/[*_`~]/g, "")
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function uniqueText(values, maxItems, maxChars) {
  const seen = new Set();
  const selected = [];
  let usedChars = 0;
  for (const value of values) {
    const cleaned = cleanInlineMarkdown(value);
    const key = cleaned.toLocaleLowerCase();
    if (!cleaned || seen.has(key)) continue;
    if (selected.length >= maxItems || usedChars + cleaned.length > maxChars) break;
    seen.add(key);
    selected.push(cleaned);
    usedChars += cleaned.length;
  }
  return selected;
}

function tableCells(line) {
  return line
    .split("|")
    .map((cell) => cleanInlineMarkdown(cell))
    .filter(Boolean);
}

function isTableDivider(line) {
  const cells = line.split("|").map((cell) => cell.trim()).filter(Boolean);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

export function extractStructure(markdown) {
  const lines = markdown.split(/\r?\n/);
  const headings = [];
  const tableHeaders = [];
  let tableCount = 0;

  for (let index = 0; index < lines.length; index += 1) {
    const trimmed = lines[index].trim();
    const markdownHeading = /^(#{1,6})\s+(.+)$/.exec(trimmed);
    if (markdownHeading) headings.push(markdownHeading[2]);

    const boldHeading = /^(?:\*\*|__)(.+?)(?:\*\*|__)\s*$/.exec(trimmed);
    if (boldHeading && !trimmed.includes("|")) headings.push(boldHeading[1]);

    if (
      trimmed.includes("|")
      && index + 1 < lines.length
      && isTableDivider(lines[index + 1])
    ) {
      tableCount += 1;
      tableHeaders.push(...tableCells(trimmed));
    }
  }

  const plainText = cleanInlineMarkdown(markdown);
  const numericCharacters = (plainText.match(/\d/g) || []).length;
  return {
    headings: uniqueText(headings, 48, 4_000),
    table_headers: uniqueText(tableHeaders, 48, 4_000),
    table_count: tableCount,
    content_chars: plainText.length,
    numeric_density: plainText.length
      ? Number((numericCharacters / plainText.length).toFixed(6))
      : 0,
    content_hash: createHash("sha256").update(markdown).digest("hex"),
  };
}

function parseWranglerResults(stdout) {
  const payload = JSON.parse(stdout);
  return payload.flatMap((entry) => entry.results || []);
}

function queryRemote(sql) {
  const output = execFileSync(
    "npx",
    ["wrangler", "d1", "execute", databaseName, "--remote", "--json", "--command", sql],
    {
      cwd: appRoot,
      encoding: "utf8",
      maxBuffer: 128 * 1024 * 1024,
      stdio: ["ignore", "pipe", "inherit"],
    },
  );
  return parseWranglerResults(output);
}

function sqlText(value) {
  if (value === null || value === undefined) return "NULL";
  return `'${String(value).replaceAll("'", "''")}'`;
}

async function loadCatalog() {
  if (process.env.STRUCTURE_INDEX_REFRESH_CATALOG !== "1") {
    const cached = await readFile(catalogPath, "utf8").catch(() => "");
    if (cached) {
      return new Map(
        cached
          .split("\n")
          .filter(Boolean)
          .map((line) => JSON.parse(line))
          .map((row) => [row.source_path, row]),
      );
    }
  }
  const tickers = queryRemote(
    "SELECT DISTINCT ticker FROM financial_documents_new WHERE ticker IS NOT NULL ORDER BY ticker",
  ).map((row) => String(row.ticker || "")).filter(Boolean);
  const rows = [];
  for (let index = 0; index < tickers.length; index += 8) {
    const batch = tickers.slice(index, index + 8);
    rows.push(...queryRemote(
      `SELECT jpg_path AS source_path, ticker AS entity, filing_period AS period,
              filing_type AS document_type, pg AS page_number
       FROM financial_documents_new
       WHERE ticker IN (${batch.map(sqlText).join(", ")})`,
    ));
  }
  await writeFile(catalogPath, rows.map((row) => JSON.stringify(row)).join("\n") + "\n");
  return new Map(rows.map((row) => [String(row.source_path), row]));
}

function filingMetadata(path, catalog) {
  const relativePath = relative(sourceRoot, path).replaceAll("\\", "/");
  const sourcePath = relativePath.replace(/\.md$/i, ".jpg");
  const mapping = catalog.get(sourcePath);
  if (!mapping) return null;
  return {
    source_path: sourcePath,
    entity: String(mapping.entity || "").toUpperCase(),
    period: String(mapping.period || ""),
    document_type: String(mapping.document_type || ""),
    page_number: Number(mapping.page_number) || null,
  };
}

async function* markdownFiles(root) {
  const directory = await opendir(root);
  for await (const entry of directory) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) yield* markdownFiles(path);
    else if (entry.isFile() && entry.name.endsWith(".md")) yield path;
  }
}

function insertSql(row) {
  return `INSERT INTO page_structures (
  source_path, entity, period, document_type, page_number, headings,
  table_headers, table_count, content_chars, numeric_density, content_hash
) VALUES (
  ${sqlText(row.source_path)}, ${sqlText(row.entity)}, ${sqlText(row.period)},
  ${sqlText(row.document_type)}, ${row.page_number ?? "NULL"}, ${sqlText(row.headings.join(" "))},
  ${sqlText(row.table_headers.join(" "))}, ${row.table_count}, ${row.content_chars},
  ${row.numeric_density}, ${sqlText(row.content_hash)}
);`;
}

async function processFile(path, catalog) {
  const metadata = filingMetadata(path, catalog);
  if (!metadata) return null;
  const markdown = await readFile(path, "utf8");
  return { ...metadata, ...extractStructure(markdown) };
}

async function main() {
  await mkdir(outputRoot, { recursive: true });
  const catalog = await loadCatalog();
  const previousSqlFiles = (await readdir(outputRoot))
    .filter((name) => /^\d{3,}-rows\.sql$/.test(name));
  await Promise.all(previousSqlFiles.map((name) => unlink(join(outputRoot, name))));
  await writeFile(
    initSqlPath,
    `DROP TABLE IF EXISTS page_structures_fts;
DROP TABLE IF EXISTS page_structures;
CREATE TABLE page_structures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_path TEXT NOT NULL UNIQUE,
  entity TEXT NOT NULL,
  period TEXT NOT NULL DEFAULT '',
  document_type TEXT NOT NULL DEFAULT '',
  page_number INTEGER,
  headings TEXT NOT NULL DEFAULT '',
  table_headers TEXT NOT NULL DEFAULT '',
  table_count INTEGER NOT NULL DEFAULT 0,
  content_chars INTEGER NOT NULL DEFAULT 0,
  numeric_density REAL NOT NULL DEFAULT 0,
  content_hash TEXT NOT NULL
);
CREATE INDEX idx_page_structures_scope
  ON page_structures(entity, period, document_type);
CREATE VIRTUAL TABLE page_structures_fts USING fts5(
  headings,
  table_headers,
  content='page_structures',
  content_rowid='id'
);
`,
  );
  await writeFile(
    finalizeSqlPath,
    `INSERT INTO page_structures_fts(page_structures_fts) VALUES('rebuild');
INSERT INTO page_structures_fts(page_structures_fts) VALUES('optimize');
`,
  );

  const rowStream = createWriteStream(rowsPath);
  let sqlStream = null;
  let sqlFileIndex = 0;
  let rowsInSqlFile = 0;
  let scanned = 0;
  let indexed = 0;
  let unmapped = 0;
  let withHeadings = 0;
  let withTableHeaders = 0;
  const pending = new Set();
  let writeChain = Promise.resolve();

  const openSqlStream = () => {
    sqlFileIndex += 1;
    rowsInSqlFile = 0;
    sqlStream = createWriteStream(
      join(outputRoot, `${String(sqlFileIndex).padStart(3, "0")}-rows.sql`),
    );
  };
  const writeRow = async (row) => {
    if (!row) return;
    if (!sqlStream || rowsInSqlFile >= rowsPerSqlFile) {
      if (sqlStream) await new Promise((resolveWrite) => sqlStream.end(resolveWrite));
      openSqlStream();
    }
    rowStream.write(`${JSON.stringify(row)}\n`);
    sqlStream.write(`${insertSql(row)}\n`);
    rowsInSqlFile += 1;
    indexed += 1;
    if (row.headings.length) withHeadings += 1;
    if (row.table_headers.length) withTableHeaders += 1;
  };

  for await (const path of markdownFiles(sourceRoot)) {
    scanned += 1;
    const task = processFile(path, catalog)
      .then((row) => {
        if (!row) unmapped += 1;
        writeChain = writeChain.then(() => writeRow(row));
        return writeChain;
      })
      .finally(() => pending.delete(task));
    pending.add(task);
    if (pending.size >= concurrency) await Promise.race(pending);
  }
  await Promise.all(pending);
  await new Promise((resolveWrite) => rowStream.end(resolveWrite));
  if (sqlStream) await new Promise((resolveWrite) => sqlStream.end(resolveWrite));

  const report = {
    generated_at: new Date().toISOString(),
    source_root: relative(projectRoot, sourceRoot),
    scanned,
    indexed,
    unmapped,
    catalog_rows: catalog.size,
    source_database: databaseName,
    with_headings: withHeadings,
    heading_coverage: indexed ? Number((withHeadings / indexed).toFixed(6)) : 0,
    with_table_headers: withTableHeaders,
    table_header_coverage: indexed ? Number((withTableHeaders / indexed).toFixed(6)) : 0,
    sql_files: sqlFileIndex + 2,
    output_root: relative(projectRoot, outputRoot),
  };
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  await main();
}
