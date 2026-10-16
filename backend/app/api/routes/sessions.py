from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CsrfPrincipal, CurrentPrincipal, DbSession, Pagination, SessionIdPath
from app.schemas.common import PATIENT_ID_PATTERN, error_responses
from app.schemas.sessions import MessagePage, SessionCreate, SessionOut, SessionPage
from app.services import sessions as service

AUTH_NOTE = (
    "\n\n**Requires sign-in.** Only the signed-in user's own sessions are visible; another user's "
    "session is indistinguishable from a missing one (404)."
)

router = APIRouter(prefix="/sessions", tags=["sessions"], responses=error_responses(401, 422, 500, 503))


@router.get("", response_model=SessionPage, summary="List my chat sessions",
            description="Sessions owned by the signed-in user, newest first." + AUTH_NOTE)
def list_sessions(
    db: DbSession,
    principal: CurrentPrincipal,
    page: Pagination,
    patient_id: Annotated[str | None, Query(pattern=PATIENT_ID_PATTERN, description="Only sessions for this patient")]
    = None,
) -> SessionPage:
    return service.list_sessions(db, principal, patient_id=patient_id, limit=page.limit, offset=page.offset)


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED, summary="Start a chat session",
             responses=error_responses(403),
             description="Create a session about one synthetic patient, owned by the signed-in user. "
                         "Requires the `X-CSRF-Token` header from `GET /api/auth/session` (403 `csrf_failed` "
                         "otherwise). An unknown `patient_id` returns 422 `unknown_patient`." + AUTH_NOTE)
def create_session(db: DbSession, principal: CsrfPrincipal, body: SessionCreate) -> SessionOut:
    return service.create_session(db, principal, body)


@router.get("/{session_id}/messages", response_model=MessagePage, summary="List messages in my session",
            responses=error_responses(404),
            description="Messages in chronological order. Messages are written by the answer pipeline from "
                        "Batch 07, so new sessions are empty for now." + AUTH_NOTE)
def list_messages(db: DbSession, principal: CurrentPrincipal, session_id: SessionIdPath,
                  page: Pagination) -> MessagePage:
    return service.list_messages(db, principal, session_id, limit=page.limit, offset=page.offset)
