"""Document and chunk use cases: listing, provenance, citations, and admin ingestion runs."""
import uuid

from sqlalchemy.orm import Session, sessionmaker

from app.errors import (
    ChunkNotFound,
    DocumentNotFound,
    IngestionInProgress,
    ManifestInvalid,
    RunNotFound,
    UnknownDocument,
)
from app.ingestion.config import IngestionSettings
from app.ingestion.manifest import ManifestError
from app.ingestion.pipeline import IngestionBusy, run_ingestion
from app.models import Chunk, Document
from app.repositories import documents as repo
from app.schemas.documents import (
    ChunkDetail,
    ChunkPage,
    ChunkSummary,
    DocumentDetail,
    DocumentPage,
    DocumentRef,
    DocumentSummary,
    IngestionRunOut,
    IngestionRunPage,
)
from app.security import Principal

PREVIEW_CHARS = 240


def citation(d: Document) -> str:
    """Assemble a citation strictly from stored metadata; missing parts are left out."""
    if d.authors:
        who = ", ".join(d.authors[:6]) + (", et al." if len(d.authors) > 6 else "")
    else:
        who = d.corporate_author or d.publisher
    parts = [f"{who}.", f"{d.title}."]
    source = d.venue or d.publisher
    if d.edition:
        source = f"{source}; {d.edition}"
    parts.append(f"{source}." if d.year is None or (d.venue and str(d.year) in d.venue)
                 else f"{source}; {d.year}.")
    if d.doi:
        parts.append(f"doi:{d.doi}.")
    if d.isbn:
        parts.append(f"ISBN {d.isbn}.")
    return " ".join(parts)


def _pages(c: Chunk) -> str:
    pdf = f"PDF p. {c.page_start}" if c.page_start == c.page_end else f"PDF pp. {c.page_start}–{c.page_end}"
    if c.page_label_start:
        printed = c.page_label_start if c.page_label_start == c.page_label_end or not c.page_label_end \
            else f"{c.page_label_start}–{c.page_label_end}"
        return f"{pdf} (printed {printed})"
    return pdf


def list_documents(db: Session, *, status: str | None, limit: int, offset: int) -> DocumentPage:
    rows, total = repo.list_documents(db, status=status, limit=limit, offset=offset)
    return DocumentPage(items=[DocumentSummary.model_validate(r) for r in rows], total=total, limit=limit,
                        offset=offset)


def get_document(db: Session, document_id: str) -> DocumentDetail:
    d = repo.get_document(db, document_id)
    if d is None:
        raise DocumentNotFound(document_id)
    return DocumentDetail.model_validate({**DocumentSummary.model_validate(d).model_dump(),
                                          **{k: getattr(d, k) for k in DocumentDetail.model_fields
                                             if k not in DocumentSummary.model_fields and k != "citation"},
                                          "citation": citation(d)})


def list_chunks(db: Session, document_id: str, *, limit: int, offset: int) -> ChunkPage:
    if repo.get_document(db, document_id) is None:
        raise DocumentNotFound(document_id)
    rows, total = repo.list_chunks(db, document_id, limit=limit, offset=offset)
    items = [ChunkSummary(chunk_id=c.chunk_id, ordinal=c.ordinal, section_path=c.section_path,
                          page_start=c.page_start, page_end=c.page_end, page_label_start=c.page_label_start,
                          page_label_end=c.page_label_end, token_count=c.token_count,
                          preview=c.text[:PREVIEW_CHARS] + ("…" if len(c.text) > PREVIEW_CHARS else ""))
             for c in rows]
    return ChunkPage(document_id=document_id, items=items, total=total, limit=limit, offset=offset)


def get_chunk(db: Session, chunk_id: str) -> ChunkDetail:
    c = repo.get_chunk(db, chunk_id)
    if c is None:
        raise ChunkNotFound(chunk_id)
    d = repo.get_document(db, c.document_id)
    prev_id, next_id = repo.neighbour_ids(db, c)
    section = " › ".join(c.section_path) if c.section_path else "no section heading"
    return ChunkDetail(
        chunk_id=c.chunk_id, ordinal=c.ordinal,
        document=DocumentRef(document_id=d.document_id, title=d.title, short_title=d.short_title,
                             citation=citation(d), doi=d.doi, license=d.license),
        section_path=c.section_path, page_start=c.page_start, page_end=c.page_end,
        page_label_start=c.page_label_start, page_label_end=c.page_label_end,
        location=f"{d.short_title} · {section} · {_pages(c)}",
        text=c.text, token_count=c.token_count, char_count=c.char_count, content_hash=c.content_hash,
        previous_chunk_id=prev_id, next_chunk_id=next_id,
    )


# ---------- admin ----------

def start_ingestion(session_factory: sessionmaker[Session], settings: IngestionSettings, principal: Principal, *,
                    force: bool, document_ids: list[str] | None) -> IngestionRunOut:
    try:
        report = run_ingestion(session_factory, settings, triggered_by=f"user:{principal.user_id}",
                               force=force, only=set(document_ids) if document_ids else None)
    except IngestionBusy as exc:
        raise IngestionInProgress(str(exc)) from None
    except ManifestError as exc:
        raise ManifestInvalid(str(exc)) from None
    except ValueError as exc:  # document ids not in the manifest
        raise UnknownDocument(str(exc)) from None
    with session_factory() as db:
        return IngestionRunOut.model_validate(repo.get_run(db, uuid.UUID(report.run_id)))


def list_runs(db: Session, *, limit: int, offset: int) -> IngestionRunPage:
    rows, total = repo.list_runs(db, limit=limit, offset=offset)
    return IngestionRunPage(items=[IngestionRunOut.model_validate(r) for r in rows], total=total, limit=limit,
                            offset=offset)


def get_run(db: Session, run_id: uuid.UUID) -> IngestionRunOut:
    r = repo.get_run(db, run_id)
    if r is None:
        raise RunNotFound()
    return IngestionRunOut.model_validate(r)
