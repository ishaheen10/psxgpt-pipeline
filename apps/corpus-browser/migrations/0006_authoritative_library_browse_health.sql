ALTER TABLE authoritative_library_collections ADD COLUMN publication_cadence TEXT;
ALTER TABLE authoritative_library_collections ADD COLUMN freshness_status TEXT;
ALTER TABLE authoritative_library_collections ADD COLUMN freshness_note TEXT;
ALTER TABLE authoritative_library_collections ADD COLUMN source_check_status TEXT;
ALTER TABLE authoritative_library_collections ADD COLUMN source_checked_at TEXT;
ALTER TABLE authoritative_library_collections ADD COLUMN source_page_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE authoritative_library_collections ADD COLUMN source_check_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE authoritative_library_collections ADD COLUMN latest_browse_date TEXT;
ALTER TABLE authoritative_library_collections ADD COLUMN latest_browse_year INTEGER;
ALTER TABLE authoritative_library_collections ADD COLUMN latest_period_end_year INTEGER;
ALTER TABLE authoritative_library_collections ADD COLUMN browse_labeled_document_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE authoritative_library_collections ADD COLUMN ambiguous_date_document_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE authoritative_library_documents ADD COLUMN browse_date TEXT;
ALTER TABLE authoritative_library_documents ADD COLUMN browse_year INTEGER;
ALTER TABLE authoritative_library_documents ADD COLUMN browse_folder TEXT;
ALTER TABLE authoritative_library_documents ADD COLUMN browse_basis TEXT;
ALTER TABLE authoritative_library_documents ADD COLUMN browse_confidence TEXT;
ALTER TABLE authoritative_library_documents ADD COLUMN period_label TEXT;
ALTER TABLE authoritative_library_documents ADD COLUMN period_start_year INTEGER;
ALTER TABLE authoritative_library_documents ADD COLUMN period_end_year INTEGER;
ALTER TABLE authoritative_library_documents ADD COLUMN source_checked_at TEXT;
ALTER TABLE authoritative_library_documents ADD COLUMN publication_cadence TEXT;

CREATE INDEX IF NOT EXISTS idx_authoritative_library_documents_browse_year
  ON authoritative_library_documents(collection_ref, browse_year DESC);
