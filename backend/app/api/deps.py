"""Shared FastAPI dependencies and validated parameter types."""
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.common import PATIENT_ID_PATTERN
from app.security import Principal, get_principal, require_csrf

DbSession = Annotated[Session, Depends(get_db)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
# Authenticated + CSRF-checked: use for every state-changing route.
CsrfPrincipal = Annotated[Principal, Depends(require_csrf)]

PatientIdPath = Annotated[str, Path(pattern=PATIENT_ID_PATTERN, description="Synthetic patient id, P-001…P-010",
                                    examples=["P-001"])]
SessionIdPath = Annotated[uuid.UUID, Path(description="Session UUID")]


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


def page_params(
    limit: Annotated[int, Query(ge=1, le=100, description="Page size (1–100)")] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000, description="Items to skip")] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


Pagination = Annotated[PageParams, Depends(page_params)]
