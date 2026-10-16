"""Research documents, chunks, and ingestion runs (schema `corpus`, migrations 0002 + 0004)."""
import uuid
from datetime import datetime

from sqlalchemy import ARRAY, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = {"schema": "corpus"}

    document_id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str]
    short_title: Mapped[str]
    publisher: Mapped[str]
    year: Mapped[int | None]
    document_type: Mapped[str]
    source_filename: Mapped[str | None]
    metadata_verified: Mapped[bool] = mapped_column(server_default="false")
    ingestion_status: Mapped[str] = mapped_column(server_default="not_ingested")
    chunk_count: Mapped[int] = mapped_column(server_default="0")
    notes: Mapped[str | None]
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    corporate_author: Mapped[str | None]
    venue: Mapped[str | None]
    edition: Mapped[str | None]
    doi: Mapped[str | None]
    isbn: Mapped[str | None]
    license: Mapped[str | None]
    verification_note: Mapped[str | None]
    file_sha256: Mapped[str | None]
    file_size_bytes: Mapped[int | None]
    page_count: Mapped[int | None]
    text_char_count: Mapped[int | None]
    storage_path: Mapped[str | None]
    extracted_metadata: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    outline: Mapped[list] = mapped_column(JSONB, server_default="[]")
    ingest_fingerprint: Mapped[str | None]
    ingested_at: Mapped[datetime | None]
    ingestion_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = {"schema": "corpus"}

    chunk_id: Mapped[str] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("corpus.documents.document_id"))
    ordinal: Mapped[int]
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    page_start: Mapped[int]
    page_end: Mapped[int]
    page_label_start: Mapped[str | None]
    page_label_end: Mapped[str | None]
    text: Mapped[str]
    token_count: Mapped[int]
    char_count: Mapped[int]
    content_hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = {"schema": "corpus"}

    run_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    triggered_by: Mapped[str]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    status: Mapped[str]
    source_dir: Mapped[str]
    manifest_path: Mapped[str]
    pipeline_version: Mapped[str]
    chunking_config: Mapped[dict] = mapped_column(JSONB)
    results: Mapped[list] = mapped_column(JSONB, server_default="[]")
