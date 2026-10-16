-- 0005 · Semantic retrieval: pgvector embeddings of document chunks
--
-- Requires the pgvector extension (https://github.com/pgvector/pgvector). Creating an
-- extension needs a privileged role, so if it is missing this migration stops with
-- instructions instead of failing halfway:
--     CREATE EXTENSION vector;      -- once per database, as a superuser
--
-- Design
--   * One vector column per window (a chunk is embedded as several overlapping windows,
--     because MiniLM reads only ~256 tokens and chunks are ~550). A chunk's score is its
--     BEST window (minimum cosine distance).
--   * The vector dimension is fixed in the column type: vector(384). Another dimension is
--     rejected by PostgreSQL.
--   * Only ONE embedding model may be active; a trigger rejects rows from any other model,
--     so embeddings from different models/versions can never be mixed in one index.
--   * Embeddings reference chunks with ON DELETE CASCADE: when ingestion replaces a
--     document's chunks, their embeddings disappear with them (no stale vectors).
--   * No ANN index: exact (sequential) cosine search on a few hundred windows is both
--     fastest and deterministic. See docs/retrieval.md for the threshold to add HNSW.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
    RAISE EXCEPTION 'pgvector is not installed on this PostgreSQL server. Install the pgvector extension for your PostgreSQL version, then run: CREATE EXTENSION vector; (as a superuser) and retry the migration. See docs/retrieval.md.';
  END IF;
END
$$;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE corpus.embedding_models (
  model_id    text PRIMARY KEY CHECK (model_id ~ '^[A-Za-z0-9._/@#+:-]{3,200}$'),
  name        text NOT NULL CHECK (btrim(name) <> ''),
  version     text NOT NULL CHECK (btrim(version) <> ''),
  dimension   integer NOT NULL CHECK (dimension > 0),
  metric      text NOT NULL DEFAULT 'cosine' CHECK (metric = 'cosine'),
  normalized  boolean NOT NULL DEFAULT true,
  settings    jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_active   boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT embedding_models_name_version_key UNIQUE (name, version, dimension)
);

COMMENT ON TABLE corpus.embedding_models IS
  'Exact embedding models (name + version + dimension) used to produce stored vectors.';

CREATE UNIQUE INDEX embedding_models_one_active ON corpus.embedding_models ((true)) WHERE is_active;

CREATE TABLE corpus.chunk_embeddings (
  chunk_id     text NOT NULL REFERENCES corpus.chunks (chunk_id) ON DELETE CASCADE,
  window_no    integer NOT NULL CHECK (window_no >= 1),
  model_id     text NOT NULL REFERENCES corpus.embedding_models (model_id),
  embedding    vector(384) NOT NULL,
  char_start   integer NOT NULL CHECK (char_start >= 0),
  char_end     integer NOT NULL,
  content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, window_no),
  CONSTRAINT chunk_embeddings_span CHECK (char_end > char_start)
);

COMMENT ON TABLE corpus.chunk_embeddings IS
  'One embedding per overlapping window of a chunk. content_hash = chunk content_hash at embedding time (stale check).';
COMMENT ON COLUMN corpus.chunk_embeddings.char_start IS
  'Window offsets into corpus.chunks.text, so the best-matching passage can be shown.';

CREATE INDEX chunk_embeddings_model_idx ON corpus.chunk_embeddings (model_id);

CREATE FUNCTION corpus.require_active_embedding_model() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM corpus.embedding_models WHERE model_id = NEW.model_id AND is_active) THEN
    RAISE EXCEPTION 'embedding model % is not the active model; mixing embedding models is not allowed', NEW.model_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER chunk_embeddings_active_model BEFORE INSERT OR UPDATE ON corpus.chunk_embeddings
  FOR EACH ROW EXECUTE FUNCTION corpus.require_active_embedding_model();

CREATE TABLE corpus.embedding_failures (
  chunk_id    text NOT NULL REFERENCES corpus.chunks (chunk_id) ON DELETE CASCADE,
  model_id    text NOT NULL REFERENCES corpus.embedding_models (model_id),
  attempts    integer NOT NULL CHECK (attempts >= 1),
  last_error  text NOT NULL,
  failed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, model_id)
);

COMMENT ON TABLE corpus.embedding_failures IS
  'Chunks that could not be embedded after all retries. They are retried by the next embed run.';
