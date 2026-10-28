PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS research_searches (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  query_text TEXT NOT NULL,
  route_json TEXT NOT NULL,
  response_json TEXT NOT NULL,
  result_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (session_id) REFERENCES research_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS research_searches_session_idx
  ON research_searches(session_id, created_at DESC);
