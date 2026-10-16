"""Database access for embeddings and vector search (raw SQL: pgvector operators)."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

# Lexical topic filters: a chunk matches a topic when its text mentions the topic's terms.
# These are keyword filters, not curated topic labels.
TOPIC_PATTERNS = {
    "alcohol": r"\yalcohol",
    "cannabis": r"\ycannabi|\ymarijuana",
    "opioids": r"\yopioid|\yheroin|\ymethadone|\ybuprenorphine|\ynaloxone",
    "stimulants": r"\ystimulant|\ycocaine|\yamphetamine|\ymethamphetamine",
    "behavioral": r"\ygambl|\ygaming\y|\yvideo game|\ybehavio(u)?ral addiction",
}


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def extension_available(db: Session) -> bool:
    return bool(db.scalar(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")))


def tables_exist(db: Session) -> bool:
    return db.scalar(text("SELECT to_regclass('corpus.chunk_embeddings')")) is not None


# ---------- models ----------

def ensure_model(db: Session, *, model_id: str, name: str, version: str, dimension: int, settings: dict) -> None:
    import json

    db.execute(text("""
        INSERT INTO corpus.embedding_models (model_id, name, version, dimension, settings)
        VALUES (:id, :name, :version, :dim, CAST(:settings AS jsonb))
        ON CONFLICT (model_id) DO NOTHING"""),
        {"id": model_id, "name": name, "version": version, "dim": dimension, "settings": json.dumps(settings)})


def models_with_embeddings(db: Session) -> list[str]:
    return list(db.scalars(text("SELECT DISTINCT model_id FROM corpus.chunk_embeddings ORDER BY 1")))


def active_model(db: Session) -> dict | None:
    row = db.execute(text("SELECT model_id, name, version, dimension, settings FROM corpus.embedding_models "
                          "WHERE is_active")).mappings().first()
    return dict(row) if row else None


def activate_model(db: Session, model_id: str) -> None:
    db.execute(text("UPDATE corpus.embedding_models SET is_active = false WHERE is_active AND model_id <> :id"),
               {"id": model_id})
    db.execute(text("UPDATE corpus.embedding_models SET is_active = true WHERE model_id = :id"), {"id": model_id})


def delete_all_embeddings(db: Session) -> None:
    db.execute(text("TRUNCATE corpus.chunk_embeddings, corpus.embedding_failures"))


# ---------- indexing ----------

def count_chunks(db: Session, document_ids: list[str] | None) -> int:
    return db.scalar(text("SELECT count(*) FROM corpus.chunks "
                          "WHERE CAST(:docs AS text[]) IS NULL OR document_id = ANY(CAST(:docs AS text[]))"),
                     {"docs": document_ids})


def chunks_needing_embeddings(db: Session, model_id: str, *, document_ids: list[str] | None) -> list[dict]:
    """Chunks with no embeddings for this model, or whose embeddings were made from older text."""
    rows = db.execute(text("""
        SELECT c.chunk_id, c.document_id, c.text, c.content_hash, c.section_path
        FROM corpus.chunks c
        WHERE (CAST(:docs AS text[]) IS NULL OR c.document_id = ANY(CAST(:docs AS text[])))
          AND NOT EXISTS (
            SELECT 1 FROM corpus.chunk_embeddings e
            WHERE e.chunk_id = c.chunk_id AND e.model_id = :m AND e.content_hash = c.content_hash)
        ORDER BY c.document_id, c.ordinal"""), {"m": model_id, "docs": document_ids}).mappings().all()
    return [dict(r) for r in rows]


def replace_chunk_embeddings(db: Session, chunk_id: str, model_id: str, content_hash: str,
                             rows: list[dict]) -> None:
    db.execute(text("DELETE FROM corpus.chunk_embeddings WHERE chunk_id = :c"), {"c": chunk_id})
    for r in rows:
        db.execute(text("""
            INSERT INTO corpus.chunk_embeddings
              (chunk_id, window_no, model_id, embedding, char_start, char_end, content_hash)
            VALUES (:c, :w, :m, CAST(:v AS vector), :s, :e, :h)"""),
            {"c": chunk_id, "w": r["window_no"], "m": model_id, "v": vector_literal(r["embedding"]),
             "s": r["char_start"], "e": r["char_end"], "h": content_hash})
    db.execute(text("DELETE FROM corpus.embedding_failures WHERE chunk_id = :c AND model_id = :m"),
               {"c": chunk_id, "m": model_id})


def record_failure(db: Session, chunk_id: str, model_id: str, attempts: int, error: str) -> None:
    db.execute(text("""
        INSERT INTO corpus.embedding_failures (chunk_id, model_id, attempts, last_error)
        VALUES (:c, :m, :a, :e)
        ON CONFLICT (chunk_id, model_id) DO UPDATE
          SET attempts = EXCLUDED.attempts, last_error = EXCLUDED.last_error, failed_at = now()"""),
        {"c": chunk_id, "m": model_id, "a": attempts, "e": error[:1000]})


def index_status(db: Session, model_id: str | None) -> dict:
    total_chunks = db.scalar(text("SELECT count(*) FROM corpus.chunks"))
    if model_id is None:
        return {"chunks": total_chunks, "embedded_chunks": 0, "windows": 0, "stale_chunks": 0, "failed_chunks": 0}
    embedded = db.scalar(text("""SELECT count(DISTINCT e.chunk_id) FROM corpus.chunk_embeddings e
                                 JOIN corpus.chunks c USING (chunk_id)
                                 WHERE e.model_id = :m AND e.content_hash = c.content_hash"""), {"m": model_id})
    stale = db.scalar(text("""SELECT count(DISTINCT e.chunk_id) FROM corpus.chunk_embeddings e
                              JOIN corpus.chunks c USING (chunk_id)
                              WHERE e.model_id = :m AND e.content_hash <> c.content_hash"""), {"m": model_id})
    windows = db.scalar(text("SELECT count(*) FROM corpus.chunk_embeddings WHERE model_id = :m"), {"m": model_id})
    failed = db.scalar(text("SELECT count(*) FROM corpus.embedding_failures WHERE model_id = :m"), {"m": model_id})
    return {"chunks": total_chunks, "embedded_chunks": embedded, "windows": windows,
            "stale_chunks": stale, "failed_chunks": failed}


# ---------- search ----------

SEARCH_SQL = """
WITH q AS (SELECT CAST(:qv AS vector(384)) AS v),
best AS (
  SELECT e.chunk_id, min(e.embedding <=> q.v) AS distance
  FROM corpus.chunk_embeddings e
  JOIN corpus.chunks c ON c.chunk_id = e.chunk_id
  JOIN corpus.documents d ON d.document_id = c.document_id
  CROSS JOIN q
  WHERE e.model_id = :m
    AND (CAST(:docs AS text[]) IS NULL OR c.document_id = ANY(CAST(:docs AS text[])))
    AND (CAST(:types AS text[]) IS NULL OR d.document_type = ANY(CAST(:types AS text[])))
    AND (CAST(:topic_re AS text) IS NULL OR c.text ~* CAST(:topic_re AS text))
  GROUP BY e.chunk_id
)
SELECT b.chunk_id, b.distance,
       c.document_id, c.ordinal, c.section_path, c.page_start, c.page_end,
       c.page_label_start, c.page_label_end, c.text, c.token_count, c.content_hash,
       d.title, d.short_title, d.year, d.document_type,
       w.char_start, w.char_end
FROM best b
JOIN corpus.chunks c ON c.chunk_id = b.chunk_id
JOIN corpus.documents d ON d.document_id = c.document_id
CROSS JOIN q
CROSS JOIN LATERAL (
  SELECT e.char_start, e.char_end FROM corpus.chunk_embeddings e
  WHERE e.chunk_id = b.chunk_id AND e.model_id = :m
  ORDER BY e.embedding <=> q.v, e.window_no LIMIT 1
) w
WHERE (CAST(:max_dist AS float8) IS NULL OR b.distance <= CAST(:max_dist AS float8))
ORDER BY b.distance, b.chunk_id
LIMIT :k
"""


def search(db: Session, *, model_id: str, query_vector: list[float], top_k: int, document_ids: list[str] | None,
           document_types: list[str] | None, topic_regex: str | None, max_distance: float | None) -> list[dict]:
    rows = db.execute(text(SEARCH_SQL), {
        "qv": vector_literal(query_vector), "m": model_id, "docs": document_ids, "types": document_types,
        "topic_re": topic_regex, "max_dist": max_distance, "k": top_k}).mappings().all()
    return [dict(r) for r in rows]
