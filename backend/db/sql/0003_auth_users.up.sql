-- 0003 · Google sign-in: users, login sessions, and real ownership of chat sessions
--
-- auth.users           One row per Google account, keyed by Google's stable subject id (sub).
--                      Email is stored and updated on each login but is NOT the identity.
-- auth.login_sessions  Server-side sessions. The browser cookie holds a random token;
--                      only an HMAC of it (keyed by SESSION_SECRET) is stored here.
-- chat.sessions        owner_id text (Batch 03 development-only principal) is replaced
--                      by owner_user_id → auth.users.

CREATE TABLE auth.users (
  user_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  google_sub     text NOT NULL UNIQUE CHECK (google_sub ~ '^[A-Za-z0-9_-]{1,255}$'),
  email          text NOT NULL CHECK (email ~ '^[^@\s]+@[^@\s]+$'),
  email_verified boolean NOT NULL,
  display_name   text CHECK (display_name IS NULL OR btrim(display_name) <> ''),
  role           text NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'instructor')),
  is_active      boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  last_login_at  timestamptz
);

COMMENT ON TABLE auth.users IS 'Google accounts that have signed in. Identity = google_sub; email may change.';
COMMENT ON COLUMN auth.users.google_sub IS 'Google OIDC subject identifier (sub claim): stable and never reused.';

CREATE INDEX users_email_idx ON auth.users (lower(email));

CREATE TRIGGER users_set_updated_at BEFORE UPDATE ON auth.users
  FOR EACH ROW EXECUTE FUNCTION cases.set_updated_at();

CREATE TABLE auth.login_sessions (
  login_session_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES auth.users (user_id) ON DELETE CASCADE,
  token_hash       bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
  csrf_token       text NOT NULL CHECK (length(csrf_token) >= 32),
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL,
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  revoked_at       timestamptz,
  CONSTRAINT login_sessions_expiry_after_creation CHECK (expires_at > created_at)
);

COMMENT ON TABLE auth.login_sessions IS
  'Server-side login sessions. token_hash = HMAC-SHA256(SESSION_SECRET, cookie token); the raw token is never stored.';

CREATE INDEX login_sessions_user_idx ON auth.login_sessions (user_id);
CREATE INDEX login_sessions_expires_idx ON auth.login_sessions (expires_at);

-- Chat sessions created under the Batch 03 development-only policy have no real owner
-- and cannot be mapped to a Google account, so they are removed.
DO $$
DECLARE n integer;
BEGIN
  DELETE FROM chat.sessions;
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n > 0 THEN
    RAISE NOTICE 'Removed % development-only chat session(s) without a real owner', n;
  END IF;
END
$$;

ALTER TABLE chat.sessions DROP COLUMN owner_id;
ALTER TABLE chat.sessions
  ADD COLUMN owner_user_id uuid NOT NULL REFERENCES auth.users (user_id) ON DELETE CASCADE;

CREATE INDEX sessions_owner_created_idx ON chat.sessions (owner_user_id, created_at DESC, session_id);
CREATE INDEX sessions_owner_patient_idx ON chat.sessions (owner_user_id, patient_id);
