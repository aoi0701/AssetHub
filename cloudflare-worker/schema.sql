CREATE TABLE IF NOT EXISTS ai_suggestion_logs (
    id TEXT PRIMARY KEY,
    asset_name TEXT NOT NULL,
    description TEXT NOT NULL,
    suggested_category_id INTEGER,
    suggested_category_name TEXT,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);
