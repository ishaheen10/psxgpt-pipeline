export type CatalogCorpus = "filing" | "announcement" | "macro";

export interface CanonicalIndustry {
  id: string;
  name: string;
  parentId: string | null;
  parentName: string | null;
  path: string;
}

export interface CatalogAvailability {
  indexed: boolean;
  parsed: boolean;
  r2_linked: boolean;
}

export interface CatalogFacetValue {
  value: string;
  label: string;
  count: number;
  selected: boolean;
}

export interface CatalogFacet {
  key: "entity" | "industry" | "period" | "corpus" | "publisher";
  label: string;
  selection_mode: "or";
  values: CatalogFacetValue[];
}

interface FacetableResult {
  corpus: CatalogCorpus;
  entity: string;
  industry_id?: string | null;
  industry?: string | null;
  period: string;
  publisher?: string | null;
}

interface FacetSelection {
  tickers: string[];
  industryTerms: string[];
  years: number[];
  corpora: CatalogCorpus[];
}

const INDUSTRY_RULES: Array<{
  pattern: RegExp;
  industry: CanonicalIndustry;
}> = [
  {
    pattern: /\b(commercial banks?|banking)\b/i,
    industry: { id: "banking", name: "Banking", parentId: "financials", parentName: "Financials", path: "Financials / Banking" },
  },
  {
    pattern: /\b(inv(?:estment)?\.? banks?|securities|investment services?)\b/i,
    industry: { id: "investment-services", name: "Investment services", parentId: "financials", parentName: "Financials", path: "Financials / Investment services" },
  },
  {
    pattern: /\b(insurance|takaful)\b/i,
    industry: { id: "insurance", name: "Insurance", parentId: "financials", parentName: "Financials", path: "Financials / Insurance" },
  },
  {
    pattern: /\b(leasing|modarabas?)\b/i,
    industry: { id: "leasing-modaraba", name: "Leasing & Modaraba", parentId: "financials", parentName: "Financials", path: "Financials / Leasing & Modaraba" },
  },
  {
    pattern: /\bcement\b/i,
    industry: { id: "cement", name: "Cement", parentId: "materials", parentName: "Materials", path: "Materials / Cement" },
  },
  {
    pattern: /\b(fertili[sz]er)\b/i,
    industry: { id: "fertilizer", name: "Fertilizer", parentId: "materials", parentName: "Materials", path: "Materials / Fertilizer" },
  },
  {
    pattern: /\bchemicals?\b/i,
    industry: { id: "chemicals", name: "Chemicals", parentId: "materials", parentName: "Materials", path: "Materials / Chemicals" },
  },
  {
    pattern: /\b(glass|ceramics?)\b/i,
    industry: { id: "glass-ceramics", name: "Glass & Ceramics", parentId: "materials", parentName: "Materials", path: "Materials / Glass & Ceramics" },
  },
  {
    pattern: /\bsteel\b/i,
    industry: { id: "steel", name: "Steel", parentId: "materials", parentName: "Materials", path: "Materials / Steel" },
  },
  {
    pattern: /\b(textile|apparel|synthetic|rayon|jute|leather|tanner)\b/i,
    industry: { id: "textiles-apparel", name: "Textiles & Apparel", parentId: "consumer", parentName: "Consumer", path: "Consumer / Textiles & Apparel" },
  },
  {
    pattern: /\b(food|personal care|vanaspati|tobacco)\b/i,
    industry: { id: "consumer-goods", name: "Consumer goods", parentId: "consumer", parentName: "Consumer", path: "Consumer / Consumer goods" },
  },
  {
    pattern: /\b(auto|automobile)\b/i,
    industry: { id: "automotive", name: "Automotive", parentId: "consumer", parentName: "Consumer", path: "Consumer / Automotive" },
  },
  {
    pattern: /\b(pharmaceutical|healthcare)\b/i,
    industry: { id: "healthcare", name: "Healthcare", parentId: "healthcare-parent", parentName: "Healthcare", path: "Healthcare / Pharmaceuticals & Care" },
  },
  {
    pattern: /\b(oil & gas marketing|omc)\b/i,
    industry: { id: "oil-gas-marketing", name: "Oil & Gas Marketing", parentId: "energy", parentName: "Energy", path: "Energy / Oil & Gas Marketing" },
  },
  {
    pattern: /\b(refiner|refineries)\b/i,
    industry: { id: "refining", name: "Refining", parentId: "energy", parentName: "Energy", path: "Energy / Refining" },
  },
  {
    pattern: /\b(upstream|exploration|gas infrastructure)\b/i,
    industry: { id: "upstream-infrastructure", name: "Upstream & Infrastructure", parentId: "energy", parentName: "Energy", path: "Energy / Upstream & Infrastructure" },
  },
  {
    pattern: /\b(power|generation|distribution)\b/i,
    industry: { id: "power", name: "Power", parentId: "energy", parentName: "Energy", path: "Energy / Power" },
  },
  {
    pattern: /\b(technology|software)\b/i,
    industry: { id: "technology", name: "Technology", parentId: "technology-communications", parentName: "Technology & Communications", path: "Technology & Communications / Technology" },
  },
  {
    pattern: /\b(telecommunication)\b/i,
    industry: { id: "telecommunications", name: "Telecommunications", parentId: "technology-communications", parentName: "Technology & Communications", path: "Technology & Communications / Telecommunications" },
  },
  {
    pattern: /\b(real estate|property|reit)\b/i,
    industry: { id: "real-estate", name: "Real estate", parentId: "real-estate-parent", parentName: "Real estate", path: "Real estate / Property & REITs" },
  },
  {
    pattern: /\b(transport|logistics|airlines)\b/i,
    industry: { id: "transport-logistics", name: "Transport & Logistics", parentId: "industrials", parentName: "Industrials", path: "Industrials / Transport & Logistics" },
  },
  {
    pattern: /\b(engineering|cable|electrical goods|packaging|paper|board)\b/i,
    industry: { id: "industrial-products", name: "Industrial products", parentId: "industrials", parentName: "Industrials", path: "Industrials / Industrial products" },
  },
  {
    pattern: /\b(conglomerate|investment holding)\b/i,
    industry: { id: "conglomerates", name: "Conglomerates", parentId: "industrials", parentName: "Industrials", path: "Industrials / Conglomerates" },
  },
];

const FALLBACK_INDUSTRY: CanonicalIndustry = {
  id: "other",
  name: "Other",
  parentId: null,
  parentName: null,
  path: "Other",
};

export function canonicalIndustry(rawIndustry: string | null | undefined): CanonicalIndustry | null {
  const raw = String(rawIndustry || "").trim();
  if (!raw) return null;
  return INDUSTRY_RULES.find((rule) => rule.pattern.test(raw))?.industry || {
    ...FALLBACK_INDUSTRY,
    name: raw.replace(/\b\w/g, (letter) => letter.toUpperCase()),
    path: raw.replace(/\b\w/g, (letter) => letter.toUpperCase()),
  };
}

export function industryMatches(rawIndustry: string, terms: string[]): boolean {
  if (!terms.length) return true;
  const canonical = canonicalIndustry(rawIndustry);
  const haystack = [
    rawIndustry,
    canonical?.id,
    canonical?.name,
    canonical?.path,
  ].filter(Boolean).join(" ").toLowerCase();
  return terms.some((term) => haystack.includes(term.toLowerCase()));
}

function facet(
  key: CatalogFacet["key"],
  label: string,
  entries: Array<{ value: string; label: string }>,
  selectedValues: Set<string>,
  maxValues = 12,
): CatalogFacet | null {
  const counts = new Map<string, { label: string; count: number }>();
  for (const entry of entries) {
    if (!entry.value) continue;
    const current = counts.get(entry.value);
    counts.set(entry.value, {
      label: entry.label,
      count: (current?.count || 0) + 1,
    });
  }
  if (counts.size <= 1) return null;
  return {
    key,
    label,
    selection_mode: "or",
    values: [...counts.entries()]
      .map(([value, item]) => ({
        value,
        label: item.label,
        count: item.count,
        selected: selectedValues.has(value),
      }))
      .sort((left, right) => right.count - left.count || left.label.localeCompare(right.label))
      .slice(0, maxValues),
  };
}

export function buildCatalogFacets(
  results: FacetableResult[],
  selected: FacetSelection,
): CatalogFacet[] {
  const facets = [
    facet(
      "entity",
      "Company or source",
      results.map((result) => ({ value: result.entity, label: result.entity })),
      new Set(selected.tickers),
    ),
    facet(
      "industry",
      "Industry",
      results
        .filter((result) => result.industry_id && result.industry)
        .map((result) => ({ value: result.industry_id!, label: result.industry! })),
      new Set(selected.industryTerms),
    ),
    facet(
      "period",
      "Period",
      results
        .map((result) => String(result.period || "").slice(0, 4))
        .filter((year) => /^\d{4}$/.test(year))
        .map((year) => ({ value: year, label: year })),
      new Set(selected.years.map(String)),
      10,
    ),
    facet(
      "corpus",
      "Source type",
      results.map((result) => ({
        value: result.corpus,
        label: result.corpus === "filing" ? "Company filings" : result.corpus === "announcement" ? "PSX announcements" : "Economic & policy",
      })),
      new Set(selected.corpora),
      3,
    ),
    facet(
      "publisher",
      "Publisher",
      results
        .filter((result) => result.publisher)
        .map((result) => ({ value: result.publisher!, label: result.publisher! })),
      new Set(),
      8,
    ),
  ];
  return facets.filter((item): item is CatalogFacet => Boolean(item));
}

export const CATALOG_SCHEMA = {
  version: "ledger-catalog.v1",
  hierarchy: ["corpus", "document", "page"],
  stable_dimensions: ["publisher", "document_type", "entity", "industry", "period", "filing_cadence"],
  searchable_keys: ["summary", "page_text", "heading"],
  value: "original_markdown",
  cart_key: ["session_id", "corpus", "page_id"],
};
