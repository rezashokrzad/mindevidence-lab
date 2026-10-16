-- 0005 down · Remove embeddings. The vector extension itself is left installed.
DROP TABLE IF EXISTS corpus.embedding_failures;
DROP TABLE IF EXISTS corpus.chunk_embeddings;
DROP FUNCTION IF EXISTS corpus.require_active_embedding_model();
DROP TABLE IF EXISTS corpus.embedding_models;
