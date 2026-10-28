ALTER TABLE authoritative_library_collections ADD COLUMN retention_policy TEXT NOT NULL DEFAULT 'recent_series';
ALTER TABLE authoritative_library_collections ADD COLUMN scope_status TEXT NOT NULL DEFAULT 'monitored';
ALTER TABLE authoritative_library_collections ADD COLUMN retention_reason TEXT;
ALTER TABLE authoritative_library_collections ADD COLUMN initial_parse_document_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE authoritative_library_collections ADD COLUMN archive_document_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE authoritative_library_collections ADD COLUMN initial_parse_page_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE authoritative_library_documents ADD COLUMN retention_policy TEXT NOT NULL DEFAULT 'recent_series';
ALTER TABLE authoritative_library_documents ADD COLUMN scope_status TEXT NOT NULL DEFAULT 'monitored';
ALTER TABLE authoritative_library_documents ADD COLUMN retention_reason TEXT;
ALTER TABLE authoritative_library_documents ADD COLUMN retention_lane TEXT NOT NULL DEFAULT 'archive_only';
ALTER TABLE authoritative_library_documents ADD COLUMN initial_parse_selected INTEGER NOT NULL DEFAULT 0;
ALTER TABLE authoritative_library_documents ADD COLUMN legal_status TEXT;

CREATE INDEX IF NOT EXISTS idx_authoritative_library_documents_retention
  ON authoritative_library_documents(initial_parse_selected, retention_lane, collection_ref);
