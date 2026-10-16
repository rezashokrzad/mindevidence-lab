-- 0003 down · Restore the Batch 03 text owner column and drop auth tables.
-- Sessions owned by real users cannot be mapped back, so they are removed.

DELETE FROM chat.sessions;
ALTER TABLE chat.sessions DROP COLUMN owner_user_id;
ALTER TABLE chat.sessions ADD COLUMN owner_id text NOT NULL CHECK (btrim(owner_id) <> '');
CREATE INDEX sessions_owner_created_idx ON chat.sessions (owner_id, created_at DESC, session_id);
CREATE INDEX sessions_owner_patient_idx ON chat.sessions (owner_id, patient_id);

DROP TABLE IF EXISTS auth.login_sessions;
DROP TABLE IF EXISTS auth.users;
