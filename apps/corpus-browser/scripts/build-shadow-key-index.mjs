#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = resolve(appRoot, "../..");
const sourceRoot = resolve(projectRoot, "artifacts/page_index_v3_20k");
const outputRoot = resolve(projectRoot, "artifacts/corpus_browser_key_ablation");
const samplePath = join(sourceRoot, "sample.jsonl");
const rowsPath = join(outputRoot, "page_keys.jsonl");
const sqlPath = join(outputRoot, "page_keys.sql");
const reportPath = join(outputRoot, "mapping_report.json");
const databaseName = process.env.CORPUS_SOURCE_D1 || "psx";
const batchSize = 200;

function sqlText(value) {
  if (value === null || value === undefined) return "NULL";
  return `'${String(value).replaceAll("'", "''")}'`;
}

function normalizedStem(path) {
  return basename(path, extname(path));
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
      maxBuffer: 64 * 1024 * 1024,
      stdio: ["ignore", "pipe", "inherit"],
    },
  );
  return parseWranglerResults(output);
}

async function loadEligibleArtifacts() {
  const sample = (await readFile(samplePath, "utf8"))
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  const loaded = [];
  for (const item of sample) {
    if (item.cohort === "authoritative") continue;
    const artifactPath = join(sourceRoot, "pages", `${item.artifact_id}.json`);
    const artifact = JSON.parse(await readFile(artifactPath, "utf8"));
    if (!artifact.output?.eligible_for_key_index) continue;
    loaded.push({
      ...item,
      artifact_path: relative(projectRoot, artifactPath),
      output: artifact.output,
    });
  }
  return loaded;
}

function chunks(values) {
  const result = [];
  for (let index = 0; index < values.length; index += batchSize) {
    result.push(values.slice(index, index + batchSize));
  }
  return result;
}

function filingIdentity(sourcePath) {
  const prefix = "markdown_pages/";
  if (!sourcePath.startsWith(prefix) || !sourcePath.endsWith(".md")) return null;
  return `${sourcePath.slice(prefix.length, -extname(sourcePath).length)}.jpg`;
}

function announcementIdentity(item) {
  const parts = String(item.trusted_metadata?.document_id || "").split("_");
  const psxId = Number(parts.at(-1));
  return Number.isSafeInteger(psxId) && psxId > 0 ? psxId : null;
}

function macroIdentity(item) {
  const metadata = item.trusted_metadata || {};
  return [
    String(metadata.document_id || ""),
    String(metadata.chapter || ""),
    String(metadata.page_number || ""),
  ].join("\u001f");
}

function loadFilingMappings(items) {
  const identities = [...new Set(items.map((item) => filingIdentity(item.source_path)).filter(Boolean))];
  const rows = [];
  for (const batch of chunks(identities)) {
    rows.push(...queryRemote(
      `SELECT id, ticker, industry, filing_type AS document_type, filing_period AS period, jpg_path AS identity
       FROM financial_documents_new
       WHERE jpg_path IN (${batch.map(sqlText).join(", ")})`,
    ));
  }
  return new Map(rows.map((row) => [String(row.identity), row]));
}

function loadAnnouncementMappings(items) {
  const identities = [...new Set(items.map(announcementIdentity).filter(Boolean))];
  const rows = [];
  for (const batch of chunks(identities)) {
    rows.push(...queryRemote(
      `SELECT id, ticker, announcement_date, announcement_time, title,
              psx_id AS identity, local_path, 'announcement' AS document_type
       FROM psx_announcements
       WHERE psx_id IN (${batch.join(", ")})`,
    ));
  }
  const unique = new Map();
  for (const row of rows) {
    const key = String(row.identity);
    if (unique.has(key)) unique.set(key, null);
    else unique.set(key, row);
  }
  return unique;
}

function loadMacroMappings() {
  const rows = queryRemote(
    `SELECT id, upper(replace(doc_type, '_', ' ')) AS entity,
            doc_type AS document_type, fiscal_year AS period,
            source_doc || char(31) || coalesce(chapter, '') || char(31) || page_num AS identity
     FROM macro_documents`,
  );
  return new Map(rows.map((row) => [String(row.identity), row]));
}

function mappedRow(item, mapping) {
  const metadata = item.trusted_metadata || {};
  const output = item.output || {};
  const corpus = String(metadata.corpus || "");
  const period = corpus === "announcement"
    ? [mapping.announcement_date, mapping.announcement_time].filter(Boolean).join(" ")
    : String(mapping.period || "");
  const entity = corpus === "macro"
    ? String(mapping.entity || "")
    : String(mapping.ticker || metadata.entity || "").toUpperCase();
  const publisher = corpus === "announcement"
    ? "Pakistan Stock Exchange"
    : corpus === "macro"
      ? String(mapping.document_type || "").replaceAll("_", " ")
      : entity;
  return {
    corpus,
    page_id: Number(mapping.id),
    entity,
    industry: String(mapping.industry || ""),
    period,
    document_type: String(mapping.document_type || metadata.collection || ""),
    publisher,
    source_path: item.source_path,
    artifact_path: item.artifact_path,
    schema_version: String(output.schema_version || "page-index.v3"),
    retrieval_key: String(output.retrieval_key || "").trim(),
    retrieval_phrases: (output.retrieval_phrases || []).map(String).filter(Boolean),
    display_summary: String(output.display_summary || "").trim(),
    parse_status: String(output.parse_status || "ok"),
    metadata_alignment: String(output.metadata_alignment || "aligned"),
  };
}

function buildSql(rows) {
  const statements = [
    "DROP TABLE IF EXISTS page_keys_fts;",
    "DROP TABLE IF EXISTS page_keys;",
    `CREATE TABLE page_keys (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      corpus TEXT NOT NULL,
      page_id INTEGER NOT NULL,
      entity TEXT NOT NULL,
      industry TEXT NOT NULL DEFAULT '',
      period TEXT NOT NULL DEFAULT '',
      document_type TEXT NOT NULL DEFAULT '',
      publisher TEXT NOT NULL DEFAULT '',
      source_path TEXT NOT NULL,
      artifact_path TEXT NOT NULL,
      schema_version TEXT NOT NULL,
      retrieval_key TEXT NOT NULL,
      retrieval_phrases TEXT NOT NULL DEFAULT '',
      display_summary TEXT NOT NULL DEFAULT '',
      parse_status TEXT NOT NULL,
      metadata_alignment TEXT NOT NULL,
      UNIQUE(corpus, page_id)
    );`,
    "CREATE INDEX idx_page_keys_scope ON page_keys(corpus, entity, period, document_type);",
    `CREATE VIRTUAL TABLE page_keys_fts USING fts5(
      retrieval_key,
      retrieval_phrases,
      content='page_keys',
      content_rowid='id'
    );`,
  ];
  for (const row of rows) {
    statements.push(
      `INSERT INTO page_keys (
        corpus, page_id, entity, industry, period, document_type, publisher,
        source_path, artifact_path, schema_version, retrieval_key,
        retrieval_phrases, display_summary, parse_status, metadata_alignment
      ) VALUES (
        ${sqlText(row.corpus)}, ${row.page_id}, ${sqlText(row.entity)}, ${sqlText(row.industry)},
        ${sqlText(row.period)}, ${sqlText(row.document_type)}, ${sqlText(row.publisher)},
        ${sqlText(row.source_path)}, ${sqlText(row.artifact_path)}, ${sqlText(row.schema_version)},
        ${sqlText(row.retrieval_key)}, ${sqlText(row.retrieval_phrases.join(" "))},
        ${sqlText(row.display_summary)}, ${sqlText(row.parse_status)}, ${sqlText(row.metadata_alignment)}
      );`,
    );
  }
  statements.push(
    "INSERT INTO page_keys_fts(rowid, retrieval_key, retrieval_phrases) SELECT id, retrieval_key, retrieval_phrases FROM page_keys;",
    "INSERT INTO page_keys_fts(page_keys_fts) VALUES('optimize');",
  );
  return `${statements.join("\n")}\n`;
}

async function main() {
  await mkdir(outputRoot, { recursive: true });
  const artifacts = await loadEligibleArtifacts();
  const eligiblePaths = new Set(artifacts.map((item) => item.source_path));
  const existingRows = await readFile(rowsPath, "utf8")
    .then((contents) => contents.split("\n").filter(Boolean).map((line) => JSON.parse(line)))
    .catch(() => []);
  const reusableRows = existingRows.filter((row) => eligiblePaths.has(row.source_path));
  const existingPaths = new Set(reusableRows.map((row) => row.source_path));
  const remainingArtifacts = artifacts.filter((item) => !existingPaths.has(item.source_path));
  const byCorpus = Map.groupBy(
    remainingArtifacts,
    (item) => String(item.trusted_metadata?.corpus || ""),
  );
  const filingMap = loadFilingMappings(byCorpus.get("filing") || []);
  const announcementMap = loadAnnouncementMappings(byCorpus.get("announcement") || []);
  const macroMap = loadMacroMappings();
  const rows = [...reusableRows];
  const misses = [];

  for (const item of remainingArtifacts) {
    const corpus = String(item.trusted_metadata?.corpus || "");
    let identity;
    let mapping;
    if (corpus === "filing") {
      identity = filingIdentity(item.source_path);
      mapping = filingMap.get(identity);
    } else if (corpus === "announcement") {
      identity = announcementIdentity(item);
      mapping = announcementMap.get(String(identity));
      if (mapping && normalizedStem(String(mapping.local_path || "")) !== item.trusted_metadata.document_id) {
        mapping = null;
      }
    } else if (corpus === "macro") {
      identity = macroIdentity(item);
      mapping = macroMap.get(identity);
    }
    if (!mapping) {
      misses.push({ corpus, identity, source_path: item.source_path });
      continue;
    }
    rows.push(mappedRow(item, mapping));
  }

  rows.sort((left, right) =>
    left.corpus.localeCompare(right.corpus)
    || left.page_id - right.page_id,
  );
  await writeFile(rowsPath, rows.map((row) => JSON.stringify(row)).join("\n") + "\n");
  await writeFile(sqlPath, buildSql(rows));

  const inputCounts = Object.fromEntries(
    [...Map.groupBy(artifacts, (item) => String(item.trusted_metadata?.corpus || ""))]
      .map(([corpus, values]) => [corpus, values.length]),
  );
  const mappedCounts = Object.fromEntries(
    [...Map.groupBy(rows, (row) => row.corpus)].map(([corpus, values]) => [corpus, values.length]),
  );
  const report = {
    generated_at: new Date().toISOString(),
    source_database: databaseName,
    source_artifacts: relative(projectRoot, sourceRoot),
    input_eligible: artifacts.length,
    reused: reusableRows.length,
    mapped: rows.length,
    mapping_rate: artifacts.length ? Number((rows.length / artifacts.length).toFixed(6)) : 0,
    input_counts: inputCounts,
    mapped_counts: mappedCounts,
    missed_counts: Object.fromEntries(
      [...Map.groupBy(misses, (row) => row.corpus)].map(([corpus, values]) => [corpus, values.length]),
    ),
    misses: misses.slice(0, 100),
    rows_sha256: createHash("sha256").update(await readFile(rowsPath)).digest("hex"),
    output_rows: relative(projectRoot, rowsPath),
    output_sql: relative(projectRoot, sqlPath),
  };
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

await main();
