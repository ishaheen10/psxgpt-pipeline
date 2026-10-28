ALTER TABLE research_sessions ADD COLUMN user_id TEXT;

CREATE INDEX IF NOT EXISTS research_sessions_user_idx
  ON research_sessions(user_id, updated_at);
