"""Administrative ingestion endpoints (instructor role only)."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status

from app.api.deps import DbSession, Pagination
from app.schemas.common import error_responses
from app.schemas.documents import IngestionRequest, IngestionRunOut, IngestionRunPage
from app.security import Principal, require_instructor, require_instructor_csrf
from app.services import documents as service

router = APIRouter(prefix="/admin/ingestion", tags=["admin"], responses=error_responses(401, 403, 422, 500, 503))


@router.post("/runs", response_model=IngestionRunOut, status_code=status.HTTP_201_CREATED,
             summary="Run document ingestion", responses={409: error_responses(409)[409]})
def start_run(request: Request, body: IngestionRequest,
              principal: Annotated[Principal, Depends(require_instructor_csrf)]):
    """Ingests the documents listed in the corpus manifest from the configured source directory.
    Idempotent: unchanged documents are skipped. Runs synchronously and returns the run record.
    Instructor only; needs `X-CSRF-Token`. 409 `ingestion_in_progress` if another run is active."""
    return service.start_ingestion(request.app.state.session_factory, request.app.state.ingestion_settings,
                                   principal, force=body.force, document_ids=body.document_ids)


@router.get("/runs", response_model=IngestionRunPage, summary="List ingestion runs",
            dependencies=[Depends(require_instructor)])
def list_runs(db: DbSession, page: Pagination):
    return service.list_runs(db, limit=page.limit, offset=page.offset)


@router.get("/runs/{run_id}", response_model=IngestionRunOut, summary="One ingestion run",
            dependencies=[Depends(require_instructor)], responses=error_responses(404))
def get_run(db: DbSession, run_id: Annotated[uuid.UUID, Path()]):
    return service.get_run(db, run_id)
