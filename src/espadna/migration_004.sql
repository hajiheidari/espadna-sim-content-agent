CREATE TABLE design_packs (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 version TEXT NOT NULL,
 digest TEXT NOT NULL,
 manifest_json TEXT NOT NULL,
 storage_path TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','approved','disabled')),
 revision INTEGER NOT NULL DEFAULT 1,
 created_by INTEGER NOT NULL REFERENCES users(telegram_id),
 reviewed_by INTEGER REFERENCES users(telegram_id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(name,version)
);
CREATE TABLE design_preferences (
 id INTEGER PRIMARY KEY CHECK(id=1),
 pack_id TEXT REFERENCES design_packs(id)
);
INSERT INTO design_preferences(id,pack_id) VALUES(1,NULL);
ALTER TABLE content_plans ADD COLUMN design_pack_id TEXT REFERENCES design_packs(id);
ALTER TABLE content_plans ADD COLUMN design_pack_revision INTEGER;
ALTER TABLE content_exports ADD COLUMN design_pack_id TEXT REFERENCES design_packs(id);
ALTER TABLE content_exports ADD COLUMN design_pack_revision INTEGER;
ALTER TABLE content_exports ADD COLUMN design_digest TEXT;
PRAGMA user_version = 4;
