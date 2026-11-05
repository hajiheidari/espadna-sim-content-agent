PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 telegram_id INTEGER PRIMARY KEY CHECK(telegram_id > 0),
 role TEXT NOT NULL CHECK(role IN ('owner','admin','viewer'))
);
CREATE TABLE IF NOT EXISTS inventory (
 phone TEXT PRIMARY KEY CHECK(length(phone)=11 AND phone GLOB '09*' AND phone NOT GLOB '*[^0-9]*'),
 sim_type INTEGER NOT NULL CHECK(sim_type IN (1,2)),
 sim_status INTEGER NOT NULL CHECK(sim_status IN (1,2,3)),
 operator TEXT CHECK(operator IN ('همراه اول','ایرانسل','رایتل')),
 price_internal TEXT,
 sale_status TEXT NOT NULL DEFAULT 'available' CHECK(sale_status IN ('available','reserved','sold')),
 revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS import_batches (
 id INTEGER PRIMARY KEY, actor INTEGER NOT NULL REFERENCES users(telegram_id),
 digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 inserted INTEGER NOT NULL, updated INTEGER NOT NULL, unchanged INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, actor INTEGER NOT NULL REFERENCES users(telegram_id),
 action TEXT NOT NULL, subject TEXT NOT NULL, before_json TEXT, after_json TEXT,
 batch_id INTEGER REFERENCES import_batches(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'append-only audit'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'append-only audit'); END;
PRAGMA user_version = 1;
