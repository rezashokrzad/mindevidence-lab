-- 0004 · Research-document ingestion
--
-- corpus.papers (Batch 03 catalogue) becomes corpus.documents and gains file,
-- extraction, and provenance columns. New tables:
--   corpus.chunks          traceable text chunks: document, section path, page range, content hash
--   corpus.ingestion_runs  one row per ingestion run, with per-document outcomes
--
-- Research documents stay in `corpus`; synthetic patient cases stay in `cases`.
-- No foreign key connects the two.

ALTER TABLE corpus.papers RENAME TO documents;
ALTER TABLE corpus.documents RENAME COLUMN paper_id TO document_id;
ALTER TABLE corpus.documents RENAME COLUMN expected_filename TO source_filename;

ALTER TABLE corpus.documents RENAME CONSTRAINT papers_pkey TO documents_pkey;
ALTER TABLE corpus.documents RENAME CONSTRAINT papers_paper_id_check TO documents_document_id_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT papers_title_check TO documents_title_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT papers_short_title_check TO documents_short_title_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT papers_publisher_check TO documents_publisher_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT papers_year_check TO documents_year_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT papers_document_type_check TO documents_document_type_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT papers_expected_filename_check TO documents_source_filename_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT papers_chunk_count_check TO documents_chunk_count_check;
ALTER TRIGGER papers_set_updated_at ON corpus.documents RENAME TO documents_set_updated_at;

ALTER TABLE corpus.documents DROP CONSTRAINT papers_ingestion_status_check;
ALTER TABLE corpus.documents ADD CONSTRAINT documents_ingestion_status_check
  CHECK (ingestion_status IN ('not_ingested', 'ingesting', 'ingested', 'failed', 'needs_ocr', 'missing_file'));

ALTER TABLE corpus.documents
  ADD COLUMN authors            text[] NOT NULL DEFAULT '{}',
  ADD COLUMN corporate_author   text,
  ADD COLUMN venue              text,
  ADD COLUMN edition            text,
  ADD COLUMN doi                text CONSTRAINT documents_doi_check CHECK (doi ~ '^10\.[0-9]{4,9}/\S+$'),
  ADD COLUMN isbn               text CONSTRAINT documents_isbn_check CHECK (isbn ~ '^[0-9][0-9-]{8,15}[0-9Xx]$'),
  ADD COLUMN license            text,
  ADD COLUMN verification_note  text,
  ADD COLUMN file_sha256        text CONSTRAINT documents_file_sha256_check CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
  ADD COLUMN file_size_bytes    bigint CHECK (file_size_bytes > 0),
  ADD COLUMN page_count         integer CHECK (page_count > 0),
  ADD COLUMN text_char_count    integer CHECK (text_char_count >= 0),
  ADD COLUMN storage_path       text,
  ADD COLUMN extracted_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN outline            jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN ingest_fingerprint text,
  ADD COLUMN ingested_at        timestamptz,
  ADD COLUMN ingestion_error    text;

CREATE UNIQUE INDEX documents_file_sha256_key ON corpus.documents (file_sha256) WHERE file_sha256 IS NOT NULL;

COMMENT ON TABLE corpus.documents IS
  'Authorized research documents (from data/corpus_manifest.json) and their ingestion state.';
COMMENT ON COLUMN corpus.documents.extracted_metadata IS
  'What the extractor found in the PDF itself (embedded metadata, DOI/ISBN/year patterns, topic mentions), kept separate from the manifest values.';
COMMENT ON COLUMN corpus.documents.ingest_fingerprint IS
  'sha256(file) + pipeline version + chunking config. Unchanged fingerprint = rerun is a no-op.';

CREATE TABLE corpus.chunks (
  chunk_id         text PRIMARY KEY CHECK (chunk_id ~ '^[A-Z0-9][A-Z0-9-]*-c[0-9]{4}$'),
  document_id      text NOT NULL REFERENCES corpus.documents (document_id) ON DELETE CASCADE,
  ordinal          integer NOT NULL CHECK (ordinal >= 1),
  section_path     text[] NOT NULL DEFAULT '{}',
  page_start       integer NOT NULL CHECK (page_start >= 1),
  page_end         integer NOT NULL,
  page_label_start text,
  page_label_end   text,
  text             text NOT NULL CHECK (btrim(text) <> ''),
  token_count      integer NOT NULL CHECK (token_count > 0),
  char_count       integer NOT NULL CHECK (char_count > 0),
  content_hash     text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chunks_page_order CHECK (page_end >= page_start),
  CONSTRAINT chunks_document_ordinal_key UNIQUE (document_id, ordinal)
);

COMMENT ON TABLE corpus.chunks IS
  'Text chunks of ingested documents. page_start/page_end are 1-based PDF page numbers; '
  'page_label_* are the printed page labels when the PDF defines them. content_hash = sha256(text).';

CREATE INDEX chunks_content_hash_idx ON corpus.chunks (content_hash);

CREATE TABLE corpus.ingestion_runs (
  run_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  triggered_by     text NOT NULL CHECK (btrim(triggered_by) <> ''),
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  status           text NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
  source_dir       text NOT NULL,
  manifest_path    text NOT NULL,
  pipeline_version text NOT NULL,
  chunking_config  jsonb NOT NULL,
  results          jsonb NOT NULL DEFAULT '[]'::jsonb
);

COMMENT ON TABLE corpus.ingestion_runs IS
  'Audit trail of ingestion runs. results holds one entry per manifest document and unlisted file.';

CREATE INDEX ingestion_runs_started_idx ON corpus.ingestion_runs (started_at DESC);
