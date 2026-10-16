"""Audit trail and activity dashboard data. Students see only their own events."""
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from app.api.deps import CsrfPrincipal, CurrentPrincipal, DbSession, Pagination
from app.schemas.audit import ActivityResponse, AuditEventPage, UiEventOut, UiEventRequest
from app.schemas.common import error_responses
from app.services import audit as service

router = APIRouter(prefix="/audit", tags=["audit"], responses=error_responses(401, 422, 500, 503))


@router.get("/events", response_model=AuditEventPage, summary="Browse audit events",
            description=("Your own audit events, newest first. Instructors can pass `scope=all`; students asking for "
                         "`all` still get only their own. Events hold ids, counts, settings and statuses, never "
                         "question text, patient narrative, OAuth tokens or API keys."))
def list_events(db: DbSession, principal: CurrentPrincipal, page: Pagination,
                scope: Annotated[Literal["own", "all"], Query()] = "own",
                correlation_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,64}$")] = None,
                session_id: uuid.UUID | None = None,
                event_type: Annotated[str | None, Query(pattern=r"^[a-z0-9_.]{1,60}$",
                                                        description="Prefix match, e.g. `retrieval` or `workflow.node`")] = None,
                min_severity: Literal["info", "warning", "error"] | None = None) -> AuditEventPage:
    return service.list_events(db, principal, scope=scope, correlation_id=correlation_id, session_id=session_id,
                               event_type=event_type, min_severity=min_severity, limit=page.limit, offset=page.offset)


@router.get("/activity", response_model=ActivityResponse, summary="Activity of the current login session",
            description=("Dashboard data: event timeline, per-answer retrieval trace, model configuration, errors and "
                         "answer latency since you signed in on this device. Own events only."))
def activity(db: DbSession, principal: CurrentPrincipal) -> ActivityResponse:
    return service.activity(db, principal)


@router.post("/ui-events", response_model=UiEventOut, summary="Record an allowed client-side event",
             responses=error_responses(403),
             description="Only `patient.selected`, `mode.changed` and `citation.opened` are accepted. Needs the CSRF token.")
def ui_event(request: Request, db: DbSession, principal: CsrfPrincipal, body: UiEventRequest) -> UiEventOut:
    return service.record_ui_event(db, request.app.state.audit, principal, body)
