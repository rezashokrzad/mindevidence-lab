"""Research documents and chunks (read-only, sign-in required)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import DbSession, Pagination
from app.schemas.common import error_responses
from app.schemas.documents import (
    CHUNK_ID_PATTERN,
    DOCUMENT_ID_PATTERN,
    ChunkDetail,
    ChunkPage,
    DocumentDetail,
    DocumentPage,
    IngestionStatus,
)
from app.security import get_principal
from app.services import documents as service

DocumentIdPath = Annotated[str, Path(pattern=DOCUMENT_ID_PATTERN, examples=["NIDA-PDAT"])]
ChunkIdPath = Annotated[str, Path(pattern=CHUNK_ID_PATTERN, examples=["NIDA-PDAT-c0007"])]

router = APIRouter(tags=["documents"], dependencies=[Depends(get_principal)],
                   responses=error_responses(401, 422, 500, 503))


@router.get("/documents", response_model=DocumentPage, summary="List research documents")
def list_documents(db: DbSession, page: Pagination,
                   status: Annotated[IngestionStatus | None, Query(description="Filter by ingestion status")] = None):
    """Authorized documents from the corpus manifest with their ingestion status."""
    return service.list_documents(db, status=status, limit=page.limit, offset=page.offset)


@router.get("/documents/{document_id}", response_model=DocumentDetail, summary="Document metadata",
            responses=error_responses(404))
def get_document(db: DbSession, document_id: DocumentIdPath):
    """Bibliographic metadata, a citation, licence, file checksum, detected outline (headings with
    PDF page numbers), and what the extractor found in the PDF itself."""
    return service.get_document(db, document_id)


@router.get("/documents/{document_id}/chunks", response_model=ChunkPage, summary="Chunks of a document",
            responses=error_responses(404))
def list_chunks(db: DbSession, document_id: DocumentIdPath, page: Pagination):
    """Chunks in document order with section, pages, and a short preview."""
    return service.list_chunks(db, document_id, limit=page.limit, offset=page.offset)


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail, summary="One chunk with its provenance",
            responses=error_responses(404))
def get_chunk(db: DbSession, chunk_id: ChunkIdPath):
    """The chunk text plus everything needed to trace it: document and citation, section path,
    PDF page range and printed page labels, content hash, and neighbouring chunk ids."""
    return service.get_chunk(db, chunk_id)
