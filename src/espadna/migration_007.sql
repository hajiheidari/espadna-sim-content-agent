ALTER TABLE inventory ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1));
ALTER TABLE import_batches ADD COLUMN mode TEXT NOT NULL DEFAULT 'merge' CHECK(mode IN ('merge','full'));
ALTER TABLE import_batches ADD COLUMN removed INTEGER NOT NULL DEFAULT 0;
CREATE TABLE inventory_sync_previews (
 id TEXT PRIMARY KEY,
 actor INTEGER NOT NULL REFERENCES users(telegram_id),
 workbook BLOB NOT NULL,
 digest TEXT NOT NULL,
 mode TEXT CHECK(mode IN ('merge','full')),
 preview_json TEXT,
 state TEXT NOT NULL DEFAULT 'uploaded' CHECK(state IN ('uploaded','previewed','applied','cancelled')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
PRAGMA user_version = 7;
