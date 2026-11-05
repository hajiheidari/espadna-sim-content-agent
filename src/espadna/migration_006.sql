ALTER TABLE inventory RENAME COLUMN phone TO phone_normalized;
ALTER TABLE inventory ADD COLUMN phone_display TEXT;
UPDATE inventory SET phone_display=phone_normalized;
PRAGMA user_version = 6;
