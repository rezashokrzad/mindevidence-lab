-- 0004 down · Back to the Batch 03 paper catalogue.

DROP TABLE IF EXISTS corpus.ingestion_runs;
DROP TABLE IF EXISTS corpus.chunks;
DROP INDEX IF EXISTS corpus.documents_file_sha256_key;

UPDATE corpus.documents SET ingestion_status = 'not_ingested', chunk_count = 0
  WHERE ingestion_status NOT IN ('not_ingested', 'ingesting', 'ingested', 'failed');

ALTER TABLE corpus.documents
  DROP COLUMN authors, DROP COLUMN corporate_author, DROP COLUMN venue, DROP COLUMN edition,
  DROP COLUMN doi, DROP COLUMN isbn, DROP COLUMN license, DROP COLUMN verification_note,
  DROP COLUMN file_sha256, DROP COLUMN file_size_bytes, DROP COLUMN page_count,
  DROP COLUMN text_char_count, DROP COLUMN storage_path, DROP COLUMN extracted_metadata,
  DROP COLUMN outline, DROP COLUMN ingest_fingerprint, DROP COLUMN ingested_at, DROP COLUMN ingestion_error;

ALTER TABLE corpus.documents DROP CONSTRAINT documents_ingestion_status_check;
ALTER TABLE corpus.documents ADD CONSTRAINT papers_ingestion_status_check
  CHECK (ingestion_status IN ('not_ingested', 'ingesting', 'ingested', 'failed'));

ALTER TRIGGER documents_set_updated_at ON corpus.documents RENAME TO papers_set_updated_at;
ALTER TABLE corpus.documents RENAME CONSTRAINT documents_pkey TO papers_pkey;
ALTER TABLE corpus.documents RENAME CONSTRAINT documents_document_id_check TO papers_paper_id_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT documents_title_check TO papers_title_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT documents_short_title_check TO papers_short_title_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT documents_publisher_check TO papers_publisher_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT documents_year_check TO papers_year_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT documents_document_type_check TO papers_document_type_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT documents_source_filename_check TO papers_expected_filename_check;
ALTER TABLE corpus.documents RENAME CONSTRAINT documents_chunk_count_check TO papers_chunk_count_check;

ALTER TABLE corpus.documents RENAME COLUMN source_filename TO expected_filename;
ALTER TABLE corpus.documents RENAME COLUMN document_id TO paper_id;
ALTER TABLE corpus.documents RENAME TO papers;
