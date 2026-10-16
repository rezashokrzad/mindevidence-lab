"""Ingestion orchestration: manifest → files → extraction → chunks → PostgreSQL.

Guarantees
* Only documents listed in the manifest are ingested; other PDFs are reported, not read.
* Idempotent: each document has a fingerprint = sha256(file) + PIPELINE_VERSION + chunking
  config. If it is unchanged and the document is already ingested, the run does nothing
  for it. Otherwise its chunks are replaced in one transaction, so a rerun never
  duplicates chunks and stable IDs (`<document_id>-c0001`, …) are reassigned
  deterministically.
* Failures are explicit: unreadable, encrypted, empty, scanned (needs OCR), checksum
  mismatch, duplicate file, and missing file each produce a recorded result. A failed
  document is never stored with empty or partial chunks.
* One run at a time (PostgreSQL advisory lock). Every run is recorded in corpus.ingestion_runs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.ingestion.chunking import Chunk, ChunkingConfig, chunk_paragraphs
from app.ingestion.config import IngestionSettings
from app.ingestion.manifest import ManifestDocument, load_manifest
from app.ingestion.pdf_extract import ExtractionError, ExtractionResult, extract
from app.ingestion.storage import sha256_file, store_original
from app.repositories import documents as repo

log = logging.getLogger(__name__)

# Bump whenever extraction or chunking logic changes so documents are re-chunked.
PIPELINE_VERSION = "1.0.0"

ERROR_ACTIONS = {"failed", "needs_ocr", "checksum_mismatch", "duplicate_file"}


class IngestionBusy(RuntimeError):
    """Another ingestion run holds the lock."""


@dataclass
class DocumentResult:
    document_id: str | None
    filename: str | None
    action: str        # ingested | reingested | unchanged | missing_file | failed | needs_ocr |
                       # checksum_mismatch | duplicate_file | unlisted_file
    message: str
    chunk_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        if self.action in ERROR_ACTIONS:
            return "error"
        if self.action in {"missing_file", "unlisted_file"} or self.warnings:
            return "warning"
        return "info"

    def as_dict(self) -> dict:
        return {**asdict(self), "severity": self.severity}


@dataclass
class RunReport:
    run_id: str
    status: str
    results: list[DocumentResult]

    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "status": self.status, "results": [r.as_dict() for r in self.results]}


def fingerprint(file_sha: str, cfg: ChunkingConfig) -> str:
    raw = f"{file_sha}|{PIPELINE_VERSION}|{json.dumps(cfg.as_dict(), sort_keys=True)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def compare_metadata(entry: ManifestDocument, ex: ExtractionResult) -> list[str]:
    """Report disagreements between the manifest and what the PDF itself says."""
    m, warnings = ex.metadata, []
    if entry.doi and m.doi and entry.doi.lower() != m.doi.lower():
        warnings.append(f"DOI in PDF ({m.doi}) differs from manifest ({entry.doi})")
    if entry.doi and not m.doi:
        warnings.append("manifest DOI not found in the PDF's first pages")
    if entry.isbn and m.isbn and re.sub(r"\D", "", entry.isbn) != re.sub(r"\D", "", m.isbn):
        warnings.append(f"ISBN in PDF ({m.isbn}) differs from manifest ({entry.isbn})")
    if entry.year and m.year_candidates and entry.year not in m.year_candidates:
        warnings.append(f"manifest year {entry.year} not among years printed in the PDF {m.year_candidates}")

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

    guess = norm(m.title_guess or m.embedded.get("title", ""))
    if guess and norm(entry.title) not in guess and guess not in norm(entry.title):
        warnings.append(f"title on the PDF's first page ({m.title_guess!r}) differs from the manifest title")
    return warnings


def _chunk_rows(document_id: str, chunks: list[Chunk], labels: list[str | None]) -> list[dict]:
    return [{
        "chunk_id": f"{document_id}-c{c.ordinal:04d}",
        "document_id": document_id,
        "ordinal": c.ordinal,
        "section_path": list(c.section_path),
        "page_start": c.page_start,
        "page_end": c.page_end,
        "page_label_start": labels[c.page_start - 1],
        "page_label_end": labels[c.page_end - 1],
        "text": c.text,
        "token_count": c.token_count,
        "char_count": len(c.text),
        "content_hash": c.content_hash,
    } for c in chunks]


def _manifest_values(entry: ManifestDocument) -> dict:
    return entry.model_dump()


def _process(db: Session, entry: ManifestDocument, settings: IngestionSettings, *, file_sha: str | None,
             force: bool, claimed_shas: dict[str, str]) -> DocumentResult:
    doc_id, cfg = entry.document_id, settings.chunking
    repo.upsert_manifest_metadata(db, doc_id, _manifest_values(entry))
    current = repo.get_document(db, doc_id)
    was_ingested = current.ingestion_status == "ingested"   # read before any update refreshes the row
    path = settings.corpus_source_dir / entry.filename if entry.filename else None

    # ---- missing source file
    if path is None or not path.is_file():
        reason = "no filename in the manifest" if path is None else f"file not found: {path.name}"
        if was_ingested:
            return DocumentResult(doc_id, entry.filename, "unchanged",
                                  f"source missing ({reason}); previously ingested chunks kept",
                                  current.chunk_count, warnings=[f"source missing: {reason}"])
        repo.set_document_state(db, doc_id, ingestion_status="missing_file", ingestion_error=reason, chunk_count=0)
        return DocumentResult(doc_id, entry.filename, "missing_file", reason)

    # ---- authorization / integrity checks
    if entry.sha256 and file_sha != entry.sha256:
        msg = (f"file checksum {file_sha[:12]}… does not match the manifest ({entry.sha256[:12]}…); "
               "not ingested. Update the manifest if this file is the authorized version.")
        repo.set_document_state(db, doc_id, ingestion_error=msg,
                                **({} if was_ingested else {"ingestion_status": "failed"}))
        return DocumentResult(doc_id, entry.filename, "checksum_mismatch", msg, current.chunk_count)
    other = repo.find_document_by_sha(db, file_sha)
    if (other and other.document_id != doc_id) or claimed_shas.get(file_sha, doc_id) != doc_id:
        owner = other.document_id if other and other.document_id != doc_id else claimed_shas[file_sha]
        msg = f"identical file already ingested as {owner}; not ingested twice"
        repo.set_document_state(db, doc_id, ingestion_status="failed", ingestion_error=msg, chunk_count=0)
        repo.replace_chunks(db, doc_id, [])
        return DocumentResult(doc_id, entry.filename, "duplicate_file", msg)
    claimed_shas[file_sha] = doc_id

    fp = fingerprint(file_sha, cfg)
    if not force and was_ingested and current.ingest_fingerprint == fp:
        return DocumentResult(doc_id, entry.filename, "unchanged", "already ingested with the same file and settings",
                              current.chunk_count)

    # ---- extraction
    try:
        ex = extract(path)
        chunks = chunk_paragraphs(ex.paragraphs, cfg)
        if not chunks:
            raise ExtractionError("empty_document", "extraction produced no chunks")
    except ExtractionError as exc:
        status = "needs_ocr" if exc.code == "needs_ocr" else "failed"
        repo.replace_chunks(db, doc_id, [])
        repo.set_document_state(db, doc_id, ingestion_status=status, ingestion_error=f"{exc.code}: {exc}",
                                chunk_count=0, file_sha256=file_sha, file_size_bytes=path.stat().st_size,
                                ingest_fingerprint=None)
        return DocumentResult(doc_id, entry.filename, status, f"{exc.code}: {exc}")

    warnings = compare_metadata(entry, ex)
    stored = store_original(path, settings.document_storage_dir, doc_id, file_sha)
    repo.replace_chunks(db, doc_id, _chunk_rows(doc_id, chunks, ex.page_labels))
    repo.set_document_state(
        db, doc_id,
        ingestion_status="ingested", ingestion_error=None, chunk_count=len(chunks),
        file_sha256=file_sha, file_size_bytes=path.stat().st_size, page_count=ex.page_count,
        text_char_count=ex.text_char_count, storage_path=stored,
        extracted_metadata={**ex.metadata.as_dict(), "quality": ex.quality, "heading_source": ex.heading_source,
                            "manifest_comparison_warnings": warnings},
        outline=[{"level": h.level, "title": h.title, "page": h.page} for h in ex.outline],
        ingest_fingerprint=fp, ingested_at=datetime.now(timezone.utc),
    )
    action = "reingested" if was_ingested else "ingested"
    return DocumentResult(doc_id, entry.filename, action,
                          f"{len(chunks)} chunks from {ex.page_count} pages ({ex.heading_source} headings)",
                          len(chunks), warnings=warnings)


def run_ingestion(session_factory: sessionmaker[Session], settings: IngestionSettings, *, triggered_by: str,
                  force: bool = False, only: set[str] | None = None) -> RunReport:
    manifest = load_manifest(settings.corpus_manifest)
    entries = [e for e in manifest.documents if not only or e.document_id in only]
    unknown = (only or set()) - {e.document_id for e in manifest.documents}
    if unknown:
        raise ValueError(f"not in the manifest: {sorted(unknown)}")

    with session_factory() as lock_db:
        if not repo.try_ingestion_lock(lock_db):
            raise IngestionBusy("another ingestion run is in progress")
        try:
            # NOTE: lock_db must stay uncommitted until the unlock, so it keeps ONE connection.
            with session_factory() as s:
                run_id = repo.create_run(
                    s, triggered_by=triggered_by, source_dir=str(settings.corpus_source_dir),
                    manifest_path=str(settings.corpus_manifest), pipeline_version=PIPELINE_VERSION,
                    chunking_config=settings.chunking.as_dict())
                s.commit()

            pdfs = {p.name: p for p in sorted(settings.corpus_source_dir.glob("*.pdf"))} \
                if settings.corpus_source_dir.is_dir() else {}
            shas = {name: sha256_file(p) for name, p in pdfs.items()}
            results: list[DocumentResult] = []
            claimed: dict[str, str] = {}

            for entry in entries:
                with session_factory() as db:
                    try:
                        result = _process(db, entry, settings, file_sha=shas.get(entry.filename or ""),
                                          force=force, claimed_shas=claimed)
                        db.commit()
                    except Exception as exc:  # unexpected: record it, keep going with other documents
                        db.rollback()
                        log.exception("ingestion of %s crashed", entry.document_id)
                        result = DocumentResult(entry.document_id, entry.filename, "failed",
                                                f"unexpected error: {type(exc).__name__}: {exc}")
                results.append(result)
                log.info("%s: %s (%s)", entry.document_id, result.action, result.message)

            listed = {e.filename for e in manifest.documents if e.filename}
            listed_shas = {e.sha256 for e in manifest.documents if e.sha256}
            if not only:
                for name in sorted(set(pdfs) - listed):
                    if shas[name] in listed_shas or shas[name] in claimed:
                        results.append(DocumentResult(None, name, "duplicate_file",
                                                      "copy of a listed document under another name; ignored"))
                    else:
                        results.append(DocumentResult(None, name, "unlisted_file",
                                                      "not in the corpus manifest, so not authorized; ignored"))

            processed = [r for r in results if r.document_id]
            errors = [r for r in results if r.severity == "error"]
            if processed and all(r.severity == "error" or r.action == "missing_file" for r in processed) \
                    and any(r.severity == "error" for r in processed):
                status = "failed"
            elif errors:
                status = "partial"
            else:
                status = "succeeded"
            with session_factory() as s:
                repo.finish_run(s, run_id, status=status, results=[r.as_dict() for r in results])
                s.commit()
            return RunReport(str(run_id), status, results)
        finally:
            repo.release_ingestion_lock(lock_db)   # same connection that took the lock
            lock_db.commit()
