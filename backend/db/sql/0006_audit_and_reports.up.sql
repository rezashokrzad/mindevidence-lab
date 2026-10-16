-- 0006 · Audit trail, student feedback, and opt-in email-report consent
--
-- audit.events          Append-only structured events (sign-in, retrieval, LLM metadata, workflow nodes,
--                       feedback, email delivery, ...). No row can be updated or deleted by the application.
--                       user_id / session_id are plain ids (no foreign keys) so deleting a user or a chat
--                       session never alters the audit history.
-- audit.feedback        Student feedback on an answer (rating, optional short comment).
-- auth.email_report_consents
--                       Per-user consent to Gmail activity summaries. The refresh token is stored ENCRYPTED
--                       (application-level Fernet key from the environment), never in plain text.
--
-- Privacy rule enforced by the application (audit service): metadata never holds OAuth tokens, API keys,
-- question text, or patient narrative; it holds ids, counts, lengths, hashes, settings, and statuses.

CREATE TABLE audit.events (
  event_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_seq       bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  user_id         uuid,
  session_id      uuid,
  correlation_id  text CHECK (correlation_id ~ '^[A-Za-z0-9._-]{1,64}$'),
  event_type      text NOT NULL CHECK (event_type ~ '^[a-z0-9_]+(\.[a-z0-9_]+)+$' AND length(event_type) <= 80),
  severity        text NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'error')),
  node            text CHECK (node ~ '^[a-z0-9_]{1,60}$'),
  status          text CHECK (status IN ('ok', 'skipped', 'failed', 'blocked', 'unchanged')),
  latency_ms      integer CHECK (latency_ms >= 0),
  patient_id      text CHECK (patient_id ~ '^P-[0-9]{3}$'),
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (pg_column_size(metadata) <= 16384)
);

COMMENT ON TABLE audit.events IS 'Append-only audit/observability events, linked per question by correlation_id.';

CREATE INDEX events_correlation_idx ON audit.events (correlation_id, event_seq) WHERE correlation_id IS NOT NULL;
CREATE INDEX events_user_time_idx ON audit.events (user_id, occurred_at DESC);
CREATE INDEX events_session_idx ON audit.events (session_id, event_seq) WHERE session_id IS NOT NULL;
CREATE INDEX events_type_idx ON audit.events (event_type, occurred_at DESC);

CREATE FUNCTION audit.forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit.events is append-only (% not allowed)', TG_OP USING ERRCODE = 'insufficient_privilege';
END
$$;

CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON audit.events
  FOR EACH ROW EXECUTE FUNCTION audit.forbid_change();

CREATE TABLE audit.feedback (
  feedback_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES auth.users (user_id) ON DELETE CASCADE,
  correlation_id text NOT NULL CHECK (correlation_id ~ '^[A-Za-z0-9._-]{1,64}$'),
  rating         text NOT NULL CHECK (rating IN ('helpful', 'not_helpful')),
  reason         text CHECK (reason IN ('inaccurate', 'missing_evidence', 'unclear', 'unsafe', 'other')),
  comment        text CHECK (comment IS NULL OR length(comment) <= 500),
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT feedback_one_per_answer UNIQUE (user_id, correlation_id)
);

COMMENT ON TABLE audit.feedback IS 'Student feedback per answer (one per user and correlation id; resubmitting replaces it).';

CREATE TABLE auth.email_report_consents (
  user_id                 uuid PRIMARY KEY REFERENCES auth.users (user_id) ON DELETE CASCADE,
  status                  text NOT NULL CHECK (status IN ('enabled', 'disabled', 'revoked', 'error')),
  granted_scope           text NOT NULL,
  encrypted_refresh_token bytea,
  token_key_id            text,
  granted_at              timestamptz NOT NULL DEFAULT now(),
  disabled_at             timestamptz,
  last_attempt_at         timestamptz,
  last_sent_at            timestamptz,
  last_status             text,
  consecutive_failures    integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT consent_token_only_when_enabled CHECK (status = 'enabled' OR encrypted_refresh_token IS NULL)
);

COMMENT ON TABLE auth.email_report_consents IS
  'Opt-in Gmail send consent. Token is encrypted; deleted on disable or revocation. Granted scope: gmail.send only.';

CREATE TRIGGER email_report_consents_set_updated_at BEFORE UPDATE ON auth.email_report_consents
  FOR EACH ROW EXECUTE FUNCTION cases.set_updated_at();
