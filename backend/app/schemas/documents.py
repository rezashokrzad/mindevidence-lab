"""Research documents, chunks, and ingestion runs. These are research texts, not patient data."""
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.common import ApiModel, Page

DOCUMENT_ID_PATTERN = r"^[A-Z0-9][A-Z0-9-]{2,63}$"
CHUNK_ID_PATTERN = r"^[A-Z0-9][A-Z0-9-]*-c[0-9]{4}$"

IngestionStatus = Literal["not_ingested", "ingesting", "ingested", "failed", "needs_ocr", "missing_file"]
DocumentType = Literal["guideline", "intervention_guide", "research_guide", "systematic_review", "other"]


class DocumentSummary(ApiModel):
    document_id: str = Field(examples=["WHO-GL-2023"])
    title: str
    short_title: str
    authors: list[str]
    corporate_author: str | None
    publisher: str
    year: int | None
    document_type: DocumentType
    metadata_verified: bool = Field(description="True when the manifest metadata was checked against the PDF")
    ingestion_status: IngestionStatus
    chunk_count: int
    page_count: int | None


class DocumentPage(Page[DocumentSummary]):
    pass


class OutlineEntry(BaseModel):
    level: int
    title: str
    page: int = Field(description="1-based PDF page number")


class DocumentDetail(DocumentSummary):
    citation: str = Field(description="Citation assembled from the manifest metadata")
    venue: str | None
    edition: str | None
    doi: str | None
    isbn: str | None
    license: str | None
    verification_note: str | None
    source_filename: str | None
    file_sha256: str | None
    file_size_bytes: int | None
    text_char_count: int | None
    ingested_at: datetime | None
    ingestion_error: str | None
    outline: list[OutlineEntry]
    extracted_metadata: dict = Field(
        description="What the extractor found in the PDF itself (kept separate from the manifest values)")


class ChunkSummary(ApiModel):
    chunk_id: str = Field(examples=["NIDA-PDAT-c0007"])
    ordinal: int
    section_path: list[str]
    page_start: int
    page_end: int
    page_label_start: str | None
    page_label_end: str | None
    token_count: int
    preview: str


class ChunkPage(Page[ChunkSummary]):
    document_id: str


class DocumentRef(BaseModel):
    document_id: str
    title: str
    short_title: str
    citation: str
    doi: str | None
    license: str | None


class ChunkDetail(BaseModel):
    """One chunk with everything needed to trace it back to its source."""

    chunk_id: str
    ordinal: int
    document: DocumentRef
    section_path: list[str] = Field(description="Headings from the document root to this chunk's section")
    page_start: int = Field(description="1-based PDF page number where the chunk starts")
    page_end: int
    page_label_start: str | None = Field(description="Page number as printed in the document, when defined")
    page_label_end: str | None
    location: str = Field(description="Human-readable provenance line")
    text: str
    token_count: int = Field(description="Estimated tokens (words + punctuation)")
    char_count: int
    content_hash: str = Field(description="sha256 of the chunk text")
    previous_chunk_id: str | None
    next_chunk_id: str | None
    source_kind: Literal["research_document"] = "research_document"


class IngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = Field(default=False, description="Re-extract even when file and settings are unchanged")
    document_ids: list[Annotated[str, StringConstraints(pattern=DOCUMENT_ID_PATTERN)]] | None = Field(
        default=None, max_length=50, description="Limit the run to these manifest documents")


class RunResult(BaseModel):
    document_id: str | None
    filename: str | None
    action: str
    severity: Literal["info", "warning", "error"]
    message: str
    chunk_count: int
    warnings: list[str]


class IngestionRunOut(ApiModel):
    run_id: uuid.UUID
    triggered_by: str
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "succeeded", "partial", "failed"]
    source_dir: str
    manifest_path: str
    pipeline_version: str
    chunking_config: dict
    results: list[RunResult]


class IngestionRunPage(Page[IngestionRunOut]):
    pass
