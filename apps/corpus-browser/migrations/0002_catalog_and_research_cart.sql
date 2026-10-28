PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS catalog_industries (
  id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  parent_id TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (parent_id) REFERENCES catalog_industries(id)
);

CREATE TABLE IF NOT EXISTS catalog_industry_aliases (
  alias TEXT PRIMARY KEY COLLATE NOCASE,
  industry_id TEXT NOT NULL,
  FOREIGN KEY (industry_id) REFERENCES catalog_industries(id)
);

INSERT OR IGNORE INTO catalog_industries (id, canonical_name, parent_id, sort_order) VALUES
  ('financials', 'Financials', NULL, 10),
  ('materials', 'Materials', NULL, 20),
  ('consumer', 'Consumer', NULL, 30),
  ('energy', 'Energy', NULL, 40),
  ('industrials', 'Industrials', NULL, 50),
  ('healthcare-parent', 'Healthcare', NULL, 60),
  ('technology-communications', 'Technology & Communications', NULL, 70),
  ('real-estate-parent', 'Real estate', NULL, 80),
  ('banking', 'Banking', 'financials', 11),
  ('investment-services', 'Investment services', 'financials', 12),
  ('insurance', 'Insurance', 'financials', 13),
  ('leasing-modaraba', 'Leasing & Modaraba', 'financials', 14),
  ('cement', 'Cement', 'materials', 21),
  ('fertilizer', 'Fertilizer', 'materials', 22),
  ('chemicals', 'Chemicals', 'materials', 23),
  ('glass-ceramics', 'Glass & Ceramics', 'materials', 24),
  ('steel', 'Steel', 'materials', 25),
  ('textiles-apparel', 'Textiles & Apparel', 'consumer', 31),
  ('consumer-goods', 'Consumer goods', 'consumer', 32),
  ('automotive', 'Automotive', 'consumer', 33),
  ('healthcare', 'Pharmaceuticals & Care', 'healthcare-parent', 61),
  ('oil-gas-marketing', 'Oil & Gas Marketing', 'energy', 41),
  ('refining', 'Refining', 'energy', 42),
  ('upstream-infrastructure', 'Upstream & Infrastructure', 'energy', 43),
  ('power', 'Power', 'energy', 44),
  ('technology', 'Technology', 'technology-communications', 71),
  ('telecommunications', 'Telecommunications', 'technology-communications', 72),
  ('real-estate', 'Property & REITs', 'real-estate-parent', 81),
  ('transport-logistics', 'Transport & Logistics', 'industrials', 51),
  ('industrial-products', 'Industrial products', 'industrials', 52),
  ('conglomerates', 'Conglomerates', 'industrials', 53);

INSERT OR IGNORE INTO catalog_industry_aliases (alias, industry_id) VALUES
  ('BANKING', 'banking'),
  ('COMMERCIAL BANKS', 'banking'),
  ('INV. BANKS / INV. COS. / SECURITIES COS.', 'investment-services'),
  ('INSURANCE', 'insurance'),
  ('LEASING COMPANIES', 'leasing-modaraba'),
  ('MODARABAS', 'leasing-modaraba'),
  ('CEMENT', 'cement'),
  ('FERTILIZER', 'fertilizer'),
  ('CHEMICAL', 'chemicals'),
  ('CHEMICALS', 'chemicals'),
  ('GLASS', 'glass-ceramics'),
  ('GLASS & CERAMICS', 'glass-ceramics'),
  ('STEEL', 'steel'),
  ('TEXTILE', 'textiles-apparel'),
  ('TEXTILE COMPOSITE', 'textiles-apparel'),
  ('TEXTILE SPINNING', 'textiles-apparel'),
  ('TEXTILE WEAVING', 'textiles-apparel'),
  ('APPAREL', 'textiles-apparel'),
  ('PHARMACEUTICALS', 'healthcare'),
  ('HEALTHCARE', 'healthcare'),
  ('OIL & GAS MARKETING COMPANIES', 'oil-gas-marketing'),
  ('OMC', 'oil-gas-marketing'),
  ('REFINERIES', 'refining'),
  ('UPSTREAM', 'upstream-infrastructure'),
  ('POWER', 'power'),
  ('POWER GENERATION & DISTRIBUTION', 'power'),
  ('TECHNOLOGY', 'technology'),
  ('TECHNOLOGY & COMMUNICATION', 'technology'),
  ('TELECOMMUNICATIONS', 'telecommunications'),
  ('REAL ESTATE INVESTMENT TRUST', 'real-estate'),
  ('PROPERTY', 'real-estate'),
  ('TRANSPORT', 'transport-logistics'),
  ('LOGISTICS', 'transport-logistics'),
  ('CONGLOMERATE', 'conglomerates');

CREATE TABLE IF NOT EXISTS research_sessions (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL DEFAULT 'Untitled analyst project',
  prompt TEXT,
  query_text TEXT,
  route_json TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'compiled', 'archived')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS research_cart_lines (
  session_id TEXT NOT NULL,
  corpus TEXT NOT NULL CHECK (corpus IN ('filing', 'announcement', 'macro')),
  page_id INTEGER NOT NULL,
  catalog_item_id TEXT NOT NULL,
  document_id TEXT,
  evidence_facet TEXT,
  entity TEXT,
  industry_id TEXT,
  industry_name TEXT,
  period TEXT,
  page_number INTEGER,
  title TEXT NOT NULL,
  source_url TEXT,
  thumbnail_url TEXT,
  asset_key TEXT,
  summary TEXT,
  snippet TEXT,
  bm25_score REAL,
  retrieval_facets_json TEXT,
  catalog_json TEXT,
  inclusion_mode TEXT NOT NULL DEFAULT 'full' CHECK (inclusion_mode IN ('summary', 'full')),
  selection_source TEXT NOT NULL DEFAULT 'manual' CHECK (selection_source IN ('manual', 'bundle', 'reader', 'similar')),
  rank_at_selection INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (session_id, corpus, page_id),
  FOREIGN KEY (session_id) REFERENCES research_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS research_cart_lines_session_idx
  ON research_cart_lines(session_id, created_at);

CREATE VIEW IF NOT EXISTS catalog_pages AS
SELECT
  'filing' AS corpus,
  id AS page_id,
  'filing:' || ticker || ':' || filing_type || ':' || filing_period AS document_id,
  ticker AS entity,
  industry AS raw_industry,
  filing_type AS document_type,
  filing_period AS period,
  pg AS page_number,
  search_text AS summary_key,
  text AS markdown_value,
  jpg_path AS asset_reference
FROM financial_documents_new
UNION ALL
SELECT
  'announcement',
  id,
  'announcement:' || id,
  ticker,
  NULL,
  'announcement',
  announcement_date,
  NULL,
  search_text,
  text,
  local_path
FROM psx_announcements
UNION ALL
SELECT
  'macro',
  id,
  'macro:' || source_doc || ':' || fiscal_year,
  upper(doc_type),
  NULL,
  doc_type,
  fiscal_year,
  page_num,
  chapter,
  content,
  source_doc || '/' || chapter || '/page_' || printf('%03d', page_num)
FROM macro_documents;

CREATE VIEW IF NOT EXISTS catalog_documents AS
SELECT
  document_id,
  corpus,
  entity,
  raw_industry,
  document_type,
  period,
  COUNT(*) AS page_count,
  MIN(page_id) AS first_page_id,
  MAX(page_id) AS last_page_id
FROM catalog_pages
GROUP BY document_id, corpus, entity, raw_industry, document_type, period;

CREATE VIEW IF NOT EXISTS catalog_search_keys AS
SELECT
  corpus,
  page_id,
  document_id,
  summary_key,
  markdown_value
FROM catalog_pages;
