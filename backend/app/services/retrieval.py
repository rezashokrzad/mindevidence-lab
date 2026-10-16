"""Semantic search use case: embed the query, run exact cosine search, shape the response.

Safety rules enforced here (not left to configuration):
* no embeddings stored, or the vector tables missing  -> 503 retrieval_unavailable
* stored embeddings from a different model/version     -> 409 embedding_model_mismatch
  (the query must be embedded by the SAME model as the index, or distances are meaningless)
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.errors import EmbeddingBackendError, EmbeddingModelMismatch, RetrievalUnavailable
from app.repositories import retrieval as repo
from app.retrieval.config import RetrievalSettings
from app.retrieval.embedder import Embedder, EmbeddingError, model_id, validate_vectors
from app.schemas.retrieval import (
    IndexStatus,
    ResultDocument,
    RetrievalSettingsOut,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

log = logging.getLogger(__name__)


def _location(row: dict) -> str:
    section = " › ".join(row["section_path"]) if row["section_path"] else "no section heading"
    pages = (f"PDF p. {row['page_start']}" if row["page_start"] == row["page_end"]
             else f"PDF pp. {row['page_start']}–{row['page_end']}")
    if row["page_label_start"]:
        pages += f" (printed {row['page_label_start']})"
    return f"{row['short_title']} · {section} · {pages}"


def effective_settings(body: SearchRequest, settings: RetrievalSettings) -> tuple[int, float | None]:
    top_k = min(body.top_k or settings.retrieval_default_top_k, settings.retrieval_max_top_k)
    min_sim = body.min_similarity if body.min_similarity is not None else settings.retrieval_default_min_similarity
    return top_k, min_sim


def _identity(embedder: Embedder) -> str:
    """model_id() may load the model (first use); a failure there is a 502, not a crash."""
    try:
        return model_id(embedder)
    except EmbeddingError as exc:
        log.warning("embedding model unavailable: %s", exc)
        raise EmbeddingBackendError(f"The embedding model is unavailable: {exc}") from None


def run_search(db: Session, embedder: Embedder, settings: RetrievalSettings, body: SearchRequest) -> SearchResponse:
    try:
        if not repo.tables_exist(db) or not repo.extension_available(db):
            raise RetrievalUnavailable("The vector index is not set up: run the 0005 migration (needs pgvector) and "
                                       "embed the corpus (python -m app.retrieval embed).")
        stored = repo.models_with_embeddings(db)
    except ProgrammingError:
        db.rollback()
        raise RetrievalUnavailable("The vector index is not available on this database.") from None
    if not stored:
        raise RetrievalUnavailable("No embeddings have been generated yet. Run: python -m app.retrieval embed")
    mid = _identity(embedder)
    if mid not in stored:
        raise EmbeddingModelMismatch(
            f"The index was built with {', '.join(stored)} but the server is configured for {mid}. "
            "Re-embed the corpus with --rebuild or fix the embedding settings.")

    top_k, min_sim = effective_settings(body, settings)
    f = body.filters
    topic_regex = "|".join(repo.TOPIC_PATTERNS[t] for t in f.topics) if f and f.topics else None
    try:
        (qv,) = validate_vectors(embedder.embed([body.query]), 1, embedder.dimension)
    except EmbeddingError as exc:
        log.warning("query embedding failed: %s", exc)
        raise EmbeddingBackendError("The embedding model could not process the query.") from None

    rows = repo.search(db, model_id=mid, query_vector=qv, top_k=top_k,
                       document_ids=f.document_ids if f else None, document_types=f.document_types if f else None,
                       topic_regex=topic_regex, max_distance=None if min_sim is None else 1.0 - min_sim)
    results = [
        SearchResult(
            rank=i, chunk_id=r["chunk_id"],
            document=ResultDocument(document_id=r["document_id"], title=r["title"], short_title=r["short_title"],
                                    year=r["year"], document_type=r["document_type"]),
            section_path=r["section_path"], page_start=r["page_start"], page_end=r["page_end"],
            page_label_start=r["page_label_start"], page_label_end=r["page_label_end"], location=_location(r),
            text=r["text"], matched_passage=r["text"][r["char_start"]:r["char_end"]],
            matched_char_start=r["char_start"], matched_char_end=r["char_end"],
            distance=round(float(r["distance"]), 6), similarity=round(1.0 - float(r["distance"]), 6),
            content_hash=r["content_hash"], token_count=r["token_count"])
        for i, r in enumerate(rows, start=1)
    ]
    return SearchResponse(
        query=body.query, results=results, result_count=len(results), candidates_considered=None,
        settings=RetrievalSettingsOut(
            embedding_model=embedder.name, embedding_model_version=embedder.version,
            embedding_dimension=embedder.dimension, top_k=top_k, max_top_k=settings.retrieval_max_top_k,
            min_similarity=min_sim, filters=f.model_dump(exclude_none=True) if f else {}))


def index_status(db: Session, embedder: Embedder) -> IndexStatus:
    try:
        if not repo.tables_exist(db):
            return IndexStatus(ready=False, embedding_model=None, embedding_model_version=None, dimension=None,
                               chunks=0, embedded_chunks=0, windows=0, stale_chunks=0, failed_chunks=0,
                               configured_model_matches=None)
        active = repo.active_model(db)
        st = repo.index_status(db, active["model_id"] if active else None)
    except ProgrammingError:
        db.rollback()
        raise RetrievalUnavailable("The vector index is not available on this database.") from None
    matches = None if not active else active["model_id"] == _identity(embedder)
    return IndexStatus(
        ready=bool(active) and st["embedded_chunks"] > 0 and st["stale_chunks"] == 0 and matches is True,
        embedding_model=active["name"] if active else None,
        embedding_model_version=active["version"] if active else None,
        dimension=active["dimension"] if active else None, configured_model_matches=matches, **st)
