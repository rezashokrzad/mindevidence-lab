"""Optional Gmail activity summaries. Everything here is opt-in; the rest of the application never depends on it."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.api.deps import CsrfPrincipal, CurrentPrincipal, DbSession
from app.schemas.common import error_responses
from app.security import (
    Principal,
    clear_gmail_tx_cookie,
    get_optional_principal,
    read_gmail_tx_cookie,
    set_gmail_tx_cookie,
)
from app.services.auth import safe_return_to, with_query

router = APIRouter(prefix="/reports/email", tags=["reports"], responses=error_responses(401, 422, 500, 503))


class EmailStatusOut(BaseModel):
    available: bool
    unavailable_reason: str | None
    enabled: bool
    status: str
    granted_scope: str | None
    granted_at: datetime | None
    last_sent_at: datetime | None
    last_status: str | None
    consecutive_failures: int
    requested_scopes: list[str]
    recipient: str
    limits: dict
    sent: list[str]
    not_sent: list[str]


class EnableOut(BaseModel):
    authorization_url: str


class DisableOut(BaseModel):
    enabled: bool
    revoked_at_google: bool | None
    note: str | None


class SendOut(BaseModel):
    sent: bool
    recipient: str
    summary: dict


@router.get("/status", response_model=EmailStatusOut, summary="Email-report consent and limits")
def email_status(request: Request, db: DbSession, principal: CurrentPrincipal):
    return request.app.state.reports.status(db, principal)


@router.post("/enable", response_model=EnableOut, summary="Start the optional Gmail consent",
             responses=error_responses(403, 503),
             description=("Returns a Google consent URL. Only when you open it and approve does Google receive a request "
                          "for the `gmail.send` permission (send only; the app can never read your mail). Identity scopes "
                          "are included only to confirm the same Google account. Needs the CSRF token."))
def enable(request: Request, principal: CsrfPrincipal):
    url, tx = request.app.state.reports.begin_enable(principal)
    response = JSONResponse({"authorization_url": url})
    set_gmail_tx_cookie(response, request.app.state.settings, tx)
    return response


@router.get("/callback", status_code=status.HTTP_303_SEE_OTHER, response_class=RedirectResponse,
            summary="Google redirects here after the Gmail consent",
            responses={303: {"description": "Back to the frontend with ?email=enabled or ?email_error=<code>"}})
def callback(request: Request, db: DbSession, principal: Annotated[Principal | None, Depends(get_optional_principal)],
             code: Annotated[str | None, Query(max_length=2048)] = None,
             state: Annotated[str | None, Query(max_length=512)] = None,
             error: Annotated[str | None, Query(max_length=200)] = None):
    settings = request.app.state.settings
    tx = read_gmail_tx_cookie(request, settings)
    target = safe_return_to(settings, None)
    result = request.app.state.reports.complete_enable(db, principal, tx, code=code, state=state, error=error)
    url = with_query(target, email="enabled") if result is None else with_query(target, email_error=result)
    response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    clear_gmail_tx_cookie(response, settings)
    return response


@router.post("/disable", response_model=DisableOut, summary="Withdraw consent",
             responses=error_responses(403),
             description="Deletes the saved permission here and revokes it at Google. Safe to call when not enabled.")
def disable(request: Request, db: DbSession, principal: CsrfPrincipal):
    return request.app.state.reports.disable(db, principal)


@router.post("/send", response_model=SendOut, summary="Send myself an activity summary now",
             responses=error_responses(403, 409, 429, 502),
             description=("Sends counts only (no questions, answers, or patient information) to your own address. "
                          "Rate limited. 409 `consent_revoked` if Google no longer honours the permission; "
                          "429 when limits or Gmail quotas apply; 502 when delivery fails. Audited either way."))
def send(request: Request, db: DbSession, principal: CsrfPrincipal):
    return request.app.state.reports.send_now(db, principal)
