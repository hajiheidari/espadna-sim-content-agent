CREATE TABLE delivery_routes (
 recipient INTEGER PRIMARY KEY REFERENCES users(telegram_id) CHECK(recipient>0),
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 created_by INTEGER NOT NULL REFERENCES users(telegram_id)
);
CREATE TABLE delivery_jobs (
 id TEXT PRIMARY KEY,
 plan_id INTEGER NOT NULL REFERENCES content_plans(id),
 plan_revision INTEGER NOT NULL,
 day TEXT NOT NULL,
 recipient INTEGER NOT NULL REFERENCES delivery_routes(recipient),
 mode TEXT NOT NULL CHECK(mode IN ('dry_run','telegram')),
 due_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','leased','sending','retry','sent','simulated','blocked','uncertain','cancelled','expired','failed')),
 attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at INTEGER NOT NULL,
 lease_token TEXT,
 lease_until INTEGER,
 export_id TEXT REFERENCES content_exports(id),
 bundle_path TEXT,
 receipt_json TEXT,
 error_code TEXT,
 created_by INTEGER NOT NULL REFERENCES users(telegram_id),
 UNIQUE(plan_id,day,recipient,mode)
);
CREATE INDEX due_deliveries ON delivery_jobs(mode,state,next_attempt_at,due_at);
CREATE TABLE delivery_attempts (
 token TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES delivery_jobs(id),
 actor INTEGER NOT NULL REFERENCES users(telegram_id),
 state TEXT NOT NULL,
 started_at INTEGER NOT NULL,
 finished_at INTEGER,
 receipt_json TEXT,
 error_code TEXT
);
PRAGMA user_version = 5;
