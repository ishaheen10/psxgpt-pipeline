CREATE TABLE IF NOT EXISTS corpus_relevance_feedback (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  session_id TEXT,
  query_text TEXT NOT NULL,
  facet TEXT,
  corpus TEXT NOT NULL CHECK (corpus IN ('filing', 'announcement', 'macro')),
  page_id INTEGER NOT NULL,
  entity TEXT,
  period TEXT,
  title TEXT,
  result_rank INTEGER,
  judgment TEXT NOT NULL CHECK (
    judgment IN ('ideal', 'relevant', 'supporting', 'irrelevant', 'wrong_scope', 'navigation_noise')
  ),
  route_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_corpus_relevance_feedback_query
  ON corpus_relevance_feedback(query_text, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_corpus_relevance_feedback_page
  ON corpus_relevance_feedback(corpus, page_id, judgment);
