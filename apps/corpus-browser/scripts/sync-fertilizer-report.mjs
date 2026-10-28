import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, "..");
const ledgerRoot = process.env.LEDGER_ROOT || path.resolve(appRoot, "../../../ledger-pakistan");
const outputPath = path.join(appRoot, "data/fertilizer-report.generated.json");

async function json(relativePath) {
  return JSON.parse(await readFile(path.join(ledgerRoot, relativePath), "utf8"));
}

function pageIds(markdown) {
  return [...new Set([...markdown.matchAll(/\[(P\d{3})\]/g)].map((match) => match[1]))].sort();
}

function stripFrontmatter(value) {
  return value.replace(/^---\n[\s\S]*?\n---\n/, "").trim();
}

const latest = await json("artifacts/25_fertilizer_section_report/latest.json");
const runRoot = `artifacts/25_fertilizer_section_report/${latest.run_id}`;
const config = await json("config/fertilizer_report_sections.json");
const manifest = await json(`${runRoot}/manifest.json`);
const monitoring = await json(`${runRoot}/monitoring.json`);
const sectionMeta = new Map(monitoring.sections.map((section) => [section.section_id, section]));
const configSections = [...config.atomic_sections, ...config.derived_sections];

const sections = [];
for (const section of configSections) {
  const answer = await readFile(path.join(ledgerRoot, runRoot, "answers", `${section.id}.md`), "utf8");
  const atomicMeta = sectionMeta.get(section.id);
  sections.push({
    id: section.id,
    title: section.title,
    question: section.question,
    kind: atomicMeta ? "atomic" : "derived",
    group: ["market_balance", "demand_affordability", "supply_gas_capacity", "price_margin_transmission"].includes(section.id)
      ? "sector"
      : ["ffc_operations", "ffc_capital_allocation", "efert_company", "fatima_company", "agritech_company"].includes(section.id)
        ? "company"
        : "synthesis",
    answer,
    page_ids: atomicMeta?.page_ids || pageIds(answer),
    page_count: atomicMeta?.page_count || 0,
    dependencies: atomicMeta?.dependencies || [],
    monitors: atomicMeta?.monitors || [],
    upstream: section.upstream || [],
    word_count: answer.trim().split(/\s+/).length,
  });
}

const sourceIndex = {};
for (const [id, source] of Object.entries(monitoring.source_index)) {
  let excerpt = "";
  try {
    excerpt = stripFrontmatter(await readFile(source.markdown_path, "utf8")).slice(0, 2600);
  } catch {
    excerpt = "Source text is retained in the corpus but was not available to the UI build.";
  }
  const usedIn = sections.filter((section) => section.page_ids.includes(id)).map((section) => section.id);
  sourceIndex[id] = { ...source, excerpt, used_in_sections: usedIn };
}

const payload = {
  schema_version: "fertilizer-report-ui.v1",
  generated_at: manifest.generated_at,
  run_id: manifest.run_id,
  prompt_version: manifest.prompt_version,
  model: manifest.model,
  pack_as_of: monitoring.pack_as_of,
  source_pack_pages: manifest.source_pack_pages,
  known_primary_gaps: monitoring.known_primary_gaps,
  dependency_to_sections: monitoring.dependency_to_sections,
  sections,
  source_index: sourceIndex,
};

await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`);
console.log(JSON.stringify({ output: path.relative(appRoot, outputPath), run_id: payload.run_id, sections: sections.length, sources: Object.keys(sourceIndex).length }, null, 2));
