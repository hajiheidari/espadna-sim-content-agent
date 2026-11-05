CREATE TABLE membership_requests (
 telegram_id INTEGER PRIMARY KEY CHECK(telegram_id>0),
 username TEXT,
 first_name TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','disabled','removed')),
 requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 reviewed_at TEXT,
 reviewed_by INTEGER REFERENCES users(telegram_id)
);
CREATE TABLE employee_settings (
 recipient INTEGER PRIMARY KEY REFERENCES users(telegram_id),
 daily_enabled INTEGER NOT NULL DEFAULT 1 CHECK(daily_enabled IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE app_settings (
 key TEXT PRIMARY KEY,
 value TEXT NOT NULL
);
INSERT INTO app_settings(key,value) VALUES('daily_employee_delivery','0');
CREATE TABLE manual_delivery_requests (
 id TEXT PRIMARY KEY,
 recipient INTEGER NOT NULL REFERENCES users(telegram_id),
 plan_id INTEGER NOT NULL REFERENCES content_plans(id),
 day TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('preparing','sending','sent','blocked','uncertain','failed')),
 created_at INTEGER NOT NULL,
 finished_at INTEGER,
 export_id TEXT REFERENCES content_exports(id),
 receipt_json TEXT,
 error_code TEXT
);
CREATE INDEX manual_delivery_recent ON manual_delivery_requests(recipient,day,created_at);
PRAGMA user_version = 8;
