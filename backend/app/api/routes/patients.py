from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, Pagination, PatientIdPath
from app.schemas.common import error_responses
from app.schemas.patients import Category, HistoryPage, PatientDetail, PatientPage
from app.security import get_principal
from app.services import patients as service

router = APIRouter(prefix="/patients", tags=["patients"], dependencies=[Depends(get_principal)],
                   responses=error_responses(401, 422, 500, 503))


@router.get("", response_model=PatientPage, summary="List synthetic patients")
def list_patients(
    db: DbSession,
    page: Pagination,
    category: Annotated[Category | None, Query(description="Filter by primary category")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100,
                                   description="Case-insensitive text search over id, concern, and summaries")] = None,
) -> PatientPage:
    """Summaries of the fictional teaching cases, ordered by id. Every record is synthetic."""
    return service.list_patients(db, category=category, q=q.strip() if q else None,
                                 limit=page.limit, offset=page.offset)


@router.get("/{patient_id}", response_model=PatientDetail, summary="Get one synthetic patient",
            responses=error_responses(404))
def get_patient(db: DbSession, patient_id: PatientIdPath) -> PatientDetail:
    """Full case record: uses, triggers, risk and protective factors, interventions, behaviours,
    progress measures, stage history, and co-occurring symptoms (descriptions, not diagnoses).

    A malformed id (not `P-` plus three digits) returns 422 `validation_error`;
    a well-formed id with no case returns 404 `patient_not_found`."""
    return service.get_patient(db, patient_id)


@router.get("/{patient_id}/history", response_model=HistoryPage, summary="Chronological history of a patient",
            responses=error_responses(404))
def get_history(db: DbSession, patient_id: PatientIdPath, page: Pagination) -> HistoryPage:
    """Timeline events in `sequence_no` order. Dates may be partial (year or month) or not recorded."""
    return service.get_history(db, patient_id, limit=page.limit, offset=page.offset)
