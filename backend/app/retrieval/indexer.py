"""Embed document chunks into pgvector.

* Idempotent: chunks whose embeddings already match the current text and model are skipped.
* Batched with bounded retries; a failing batch is retried chunk by chunk so one bad chunk
  cannot block the rest. Chunks that still fail are recorded in corpus.embedding_failures
  and reported, never silently skipped, and are retried on the next run.
* Never mixes models: if embeddings from a different model/version exist, it refuses unless
  `rebuild=True`, which deletes all existing embeddings first.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from app.ingestion.pipeline import IngestionBusy
from app.repositories import documents as docs_repo
from app.repositories import retrieval as repo
from app.retrieval.config import RetrievalSettings
from app.retrieval.embedder import Embedder, EmbeddingError, model_id, validate_vectors
from app.retrieval.windows import split_windows

log = logging.getLogger(__name__)


class ModelMismatch(RuntimeError):
    """Stored embeddings come from a different model than the configured one."""


@dataclass
class EmbedReport:
    model_id: str
    embedded_chunks: int = 0
    skipped_chunks: int = 0
    windows: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)   # (chunk_id, error)
    rebuilt: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed


def _embed_with_retries(embedder: Embedder, texts: list[str], settings: RetrievalSettings,
                        sleep=time.sleep) -> tuple[list[list[float]] | None, int, str]:
    """Return (vectors | None, attempts, last_error)."""
    last = ""
    for attempt in range(1, settings.embedding_max_retries + 2):
        try:
            return validate_vectors(embedder.embed(texts), len(texts), embedder.dimension), attempt, ""
        except Exception as exc:  # embedder-specific failures: retry, then report
            last = f"{type(exc).__name__}: {exc}"
            if attempt <= settings.embedding_max_retries:
                sleep(settings.embedding_retry_backoff_seconds * 2 ** (attempt - 1))
    return None, settings.embedding_max_retries + 1, last


def _prepare(chunk: dict, settings: RetrievalSettings):
    """Windows plus the model input for each (section heading as context, not stored in offsets)."""
    heading = chunk["section_path"][-1] if chunk["section_path"] else ""
    windows = split_windows(chunk["text"], settings.embedding_window_words, settings.embedding_window_overlap_words)
    inputs = [f"{heading}. {w.text}" if heading else w.text for w in windows]
    return windows, inputs


def embed_corpus(session_factory: sessionmaker[Session], embedder: Embedder, settings: RetrievalSettings, *,
                 rebuild: bool = False, document_ids: list[str] | None = None, sleep=time.sleep) -> EmbedReport:
    if embedder.dimension != settings.embedding_dimension:
        raise EmbeddingError(f"embedder dimension {embedder.dimension} != configured {settings.embedding_dimension}")
    mid = model_id(embedder)                      # for the real model this loads it (and fails early)
    report = EmbedReport(model_id=mid)

    with session_factory() as lock_db:
        if not docs_repo.try_ingestion_lock(lock_db):     # same lock as ingestion: chunks must not change underneath us
            raise IngestionBusy("ingestion or another embedding run is in progress")
        try:
            with session_factory() as db:
                others = [m for m in repo.models_with_embeddings(db) if m != mid]
                if others and not rebuild:
                    raise ModelMismatch(
                        f"embeddings from a different model already exist ({', '.join(others)}); "
                        f"the configured model is {mid}. Re-run with --rebuild to replace them all.")
                if rebuild:
                    repo.delete_all_embeddings(db)
                    report.rebuilt = True
                repo.ensure_model(db, model_id=mid, name=embedder.name, version=embedder.version,
                                  dimension=embedder.dimension,
                                  settings={"window_words": settings.embedding_window_words,
                                            "window_overlap_words": settings.embedding_window_overlap_words,
                                            "normalized": True, "metric": "cosine",
                                            "revision_setting": settings.embedding_model_revision or None})
                repo.activate_model(db, mid)
                db.commit()
                pending = repo.chunks_needing_embeddings(db, mid, document_ids=document_ids)
                report.skipped_chunks = repo.count_chunks(db, document_ids) - len(pending)

            bs = settings.embedding_batch_size
            for i in range(0, len(pending), bs):
                batch = pending[i:i + bs]
                prepared = [_prepare(c, settings) for c in batch]
                flat = [t for _, inputs in prepared for t in inputs]
                vectors, attempts, error = _embed_with_retries(embedder, flat, settings, sleep)
                if vectors is None and len(batch) > 1:      # isolate the bad chunk(s)
                    _embed_individually(session_factory, embedder, settings, batch, prepared, mid, report, sleep)
                    continue
                _store(session_factory, batch, prepared, vectors, attempts, error, mid, report)
                log.info("embedded %d/%d chunks", min(i + bs, len(pending)), len(pending))
            return report
        finally:
            docs_repo.release_ingestion_lock(lock_db)
            lock_db.commit()


def _store(session_factory, batch, prepared, vectors, attempts, error, mid, report):
    with session_factory() as db:
        if vectors is None:
            for c in batch:
                repo.record_failure(db, c["chunk_id"], mid, attempts, error)
                report.failed.append((c["chunk_id"], error))
            db.commit()
            return
        pos = 0
        for c, (windows, _inputs) in zip(batch, prepared):
            rows = [{"window_no": w.number, "char_start": w.start, "char_end": w.end, "embedding": vectors[pos + k]}
                    for k, w in enumerate(windows)]
            pos += len(windows)
            repo.replace_chunk_embeddings(db, c["chunk_id"], mid, c["content_hash"], rows)
            report.embedded_chunks += 1
            report.windows += len(rows)
        db.commit()


def _embed_individually(session_factory, embedder, settings, batch, prepared, mid, report, sleep):
    for c, p in zip(batch, prepared):
        vectors, attempts, error = _embed_with_retries(embedder, p[1], settings, sleep)
        _store(session_factory, [c], [p], vectors, attempts, error, mid, report)
