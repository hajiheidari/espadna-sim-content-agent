ALTER TABLE users ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1));
CREATE TABLE IF NOT EXISTS content_plans (
 id INTEGER PRIMARY KEY,
 month TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','approved')),
 revision INTEGER NOT NULL DEFAULT 1,
 created_by INTEGER NOT NULL REFERENCES users(telegram_id),
 approved_by INTEGER REFERENCES users(telegram_id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS content_slots (
 id INTEGER PRIMARY KEY,
 plan_id INTEGER NOT NULL REFERENCES content_plans(id),
 day TEXT NOT NULL,
 position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 6),
 template_id INTEGER NOT NULL CHECK(template_id BETWEEN 1 AND 30),
 phone TEXT NOT NULL REFERENCES inventory(phone),
 inventory_revision INTEGER NOT NULL,
 UNIQUE(plan_id,day,position),
 UNIQUE(plan_id,day,phone)
);
CREATE INDEX IF NOT EXISTS slots_phone ON content_slots(phone);
CREATE TABLE IF NOT EXISTS content_exports (
 id TEXT PRIMARY KEY,
 actor INTEGER NOT NULL REFERENCES users(telegram_id),
 plan_id INTEGER NOT NULL REFERENCES content_plans(id),
 plan_revision INTEGER NOT NULL,
 digest TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS export_items (
 export_id TEXT NOT NULL REFERENCES content_exports(id),
 phone TEXT NOT NULL REFERENCES inventory(phone),
 inventory_revision INTEGER NOT NULL,
 PRIMARY KEY(export_id,phone)
);
PRAGMA user_version = 2;
