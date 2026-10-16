"""Database access for research documents, chunks, and ingestion runs."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Chunk, Document, IngestionRun

# Arbitrary constant key for pg_advisory_lock: one ingestion run at a time per database.
INGESTION_LOCK_KEY = 0x4D45_4C5F_494E  # "MEL_IN"

MANIFEST_COLUMNS = ("title", "short_title", "authors", "corporate_author", "publisher", "year", "venue",
                    "edition", "doi", "isbn", "document_type", "license", "metadata_verified",
                    "verification_note")


# ---------- reads ----------

def list_documents(db: Session, *, status: str | None, limit: int, offset: int) -> tuple[list[Document], int]:
    where = [Document.ingestion_status == status] if status else []
    total = db.scalar(select(func.count()).select_from(Document).where(*where))
    rows = db.scalars(select(Document).where(*where).order_by(Document.document_id).limit(limit).offset(offset)).all()
    return list(rows), total or 0


def get_document(db: Session, document_id: str) -> Document | None:
    return db.get(Document, document_id)


def list_chunks(db: Session, document_id: str, *, limit: int, offset: int) -> tuple[list[Chunk], int]:
    total = db.scalar(select(func.count()).where(Chunk.document_id == document_id))
    rows = db.scalars(select(Chunk).where(Chunk.document_id == document_id)
                      .order_by(Chunk.ordinal).limit(limit).offset(offset)).all()
    return list(rows), total or 0


def get_chunk(db: Session, chunk_id: str) -> Chunk | None:
    return db.get(Chunk, chunk_id)


def neighbour_ids(db: Session, chunk: Chunk) -> tuple[str | None, str | None]:
    def at(ordinal: int):
        return db.scalar(select(Chunk.chunk_id).where(Chunk.document_id == chunk.document_id,
                                                      Chunk.ordinal == ordinal))
    return at(chunk.ordinal - 1), at(chunk.ordinal + 1)


def find_document_by_sha(db: Session, sha256: str) -> Document | None:
    return db.scalars(select(Document).where(Document.file_sha256 == sha256)).first()


def list_runs(db: Session, *, limit: int, offset: int) -> tuple[list[IngestionRun], int]:
    total = db.scalar(select(func.count()).select_from(IngestionRun))
    rows = db.scalars(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit).offset(offset)).all()
    return list(rows), total or 0


def get_run(db: Session, run_id: uuid.UUID) -> IngestionRun | None:
    return db.get(IngestionRun, run_id)


# ---------- ingestion writes ----------

def try_ingestion_lock(db: Session) -> bool:
    """Session-level advisory lock; released by release_ingestion_lock or when the connection closes."""
    return bool(db.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": INGESTION_LOCK_KEY}))


def release_ingestion_lock(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": INGESTION_LOCK_KEY})


def upsert_manifest_metadata(db: Session, document_id: str, values: dict) -> None:
    """Create the document row or refresh its bibliographic fields. Ingestion state is untouched."""
    row = {"document_id": document_id, **{k: values[k] for k in MANIFEST_COLUMNS}}
    stmt = pg_insert(Document).values(**row, source_filename=values.get("filename"))
    stmt = stmt.on_conflict_do_update(
        index_elements=[Document.document_id],
        set_={**{k: stmt.excluded[k] for k in MANIFEST_COLUMNS}, "source_filename": stmt.excluded.source_filename},
    )
    db.execute(stmt)


def set_document_state(db: Session, document_id: str, **values) -> None:
    db.execute(update(Document).where(Document.document_id == document_id).values(**values))


def replace_chunks(db: Session, document_id: str, rows: list[dict]) -> None:
    db.execute(delete(Chunk).where(Chunk.document_id == document_id))
    if rows:
        db.execute(insert(Chunk), rows)


def create_run(db: Session, **values) -> uuid.UUID:
    return db.scalar(insert(IngestionRun).values(status="running", **values).returning(IngestionRun.run_id))


def finish_run(db: Session, run_id: uuid.UUID, *, status: str, results: list[dict]) -> None:
    db.execute(update(IngestionRun).where(IngestionRun.run_id == run_id)
               .values(status=status, results=results, finished_at=datetime.now(timezone.utc)))
