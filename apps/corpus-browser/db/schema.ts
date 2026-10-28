/**
 * Durable catalog additions. Existing corpus tables remain the source of truth;
 * catalog_documents, catalog_pages and catalog_search_keys are logical views
 * created by migration 0002 instead of duplicated page inventories.
 */
export const catalogSchema = {
  industries: "catalog_industries",
  industryAliases: "catalog_industry_aliases",
  documents: "catalog_documents",
  pages: "catalog_pages",
  searchKeys: "catalog_search_keys",
  researchSessions: "research_sessions",
  researchSearches: "research_searches",
  researchCartLines: "research_cart_lines",
} as const;

export type InclusionMode = "summary" | "full";
export type SelectionSource = "manual" | "bundle" | "reader" | "similar";
