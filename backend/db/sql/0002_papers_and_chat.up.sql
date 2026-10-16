-- 0002 · Research-paper catalogue and chat sessions/messages
--
-- corpus.papers  Bibliographic metadata for the authorized course documents.
--                Chunks and embeddings arrive in Batches 05–06.
-- chat.*         Research chat sessions and their messages.
--
-- Cross-schema references (chat.sessions.patient_id → cases.patients) are kept as
-- validated text, NOT foreign keys. That lets the synthetic cases be re-seeded
-- (TRUNCATE) without touching user data. The API checks the patient exists.

-- ---------- corpus.papers ----------

CREATE TABLE corpus.papers (
  paper_id          text PRIMARY KEY CHECK (paper_id ~ '^[A-Z0-9][A-Z0-9-]{2,63}$'),
  title             text NOT NULL CHECK (btrim(title) <> ''),
  short_title       text NOT NULL CHECK (btrim(short_title) <> ''),
  publisher         text NOT NULL CHECK (btrim(publisher) <> ''),
  year              smallint CHECK (year BETWEEN 1950 AND 2100),
  document_type     text NOT NULL
                    CHECK (document_type IN ('guideline', 'intervention_guide', 'research_guide', 'systematic_review', 'other')),
  expected_filename text CHECK (expected_filename ~ '^[A-Za-z0-9._-]+\.pdf$'),
  metadata_verified boolean NOT NULL DEFAULT false,
  ingestion_status  text NOT NULL DEFAULT 'not_ingested'
                    CHECK (ingestion_status IN ('not_ingested', 'ingesting', 'ingested', 'failed')),
  chunk_count       integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
  notes             text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE corpus.papers IS
  'Authorized research documents. metadata_verified=false until checked against the ingested PDF (Batch 05).';

CREATE TRIGGER papers_set_updated_at BEFORE UPDATE ON corpus.papers
  FOR EACH ROW EXECUTE FUNCTION cases.set_updated_at();

-- ---------- chat ----------

CREATE SCHEMA IF NOT EXISTS chat;
COMMENT ON SCHEMA chat IS 'Research chat sessions and messages. Separate from patient cases and audit logs.';

CREATE TABLE chat.sessions (
  session_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Owner principal. Development-only policy until Batch 04: a fixed local id
  -- such as 'dev-local'. Batch 04 maps this to authenticated users.
  owner_id     text NOT NULL CHECK (btrim(owner_id) <> ''),
  patient_id   text NOT NULL CHECK (patient_id ~ '^P-[0-9]{3}$'),
  title        text CHECK (title IS NULL OR btrim(title) <> ''),
  mode         text NOT NULL DEFAULT 'rag' CHECK (mode IN ('gk', 'rag', 'both')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE chat.sessions IS 'A research conversation about one synthetic patient case.';

CREATE INDEX sessions_owner_created_idx ON chat.sessions (owner_id, created_at DESC, session_id);
CREATE INDEX sessions_owner_patient_idx ON chat.sessions (owner_id, patient_id);

CREATE TRIGGER sessions_set_updated_at BEFORE UPDATE ON chat.sessions
  FOR EACH ROW EXECUTE FUNCTION cases.set_updated_at();

CREATE TABLE chat.messages (
  message_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  message_seq     bigint GENERATED ALWAYS AS IDENTITY UNIQUE,  -- stable ordering
  session_id      uuid NOT NULL REFERENCES chat.sessions (session_id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('user', 'assistant')),
  content         text NOT NULL CHECK (btrim(content) <> ''),
  mode            text NOT NULL CHECK (mode IN ('gk', 'rag', 'both')),
  correlation_id  text CHECK (correlation_id ~ '^[A-Za-z0-9._-]{1,64}$'),
  created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE chat.messages IS
  'Messages in a chat session. Written by the answer pipeline from Batch 07; read-only in Batch 03.';

CREATE INDEX messages_session_seq_idx ON chat.messages (session_id, message_seq);
