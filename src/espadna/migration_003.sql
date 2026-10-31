ALTER TABLE content_exports ADD COLUMN state TEXT NOT NULL DEFAULT 'ready' CHECK(state IN ('pending','ready','failed'));
CREATE TABLE artifact_files (
 export_id TEXT NOT NULL REFERENCES content_exports(id),
 filename TEXT NOT NULL,
 digest TEXT NOT NULL,
 PRIMARY KEY(export_id,filename)
);
PRAGMA user_version = 3;
