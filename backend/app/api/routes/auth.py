"""Google sign-in endpoints.

Browser flow:
  1. Frontend navigates to GET /api/auth/login?return_to=<frontend URL>
  2. API redirects to Google (scopes: openid email profile) with state, nonce, PKCE
  3. Google redirects to GET /api/auth/callback?code&state
  4. API verifies everything, sets the HttpOnly session cookie, redirects to return_to
     (with ?signin=success, or ?auth_error=<code> on failure)
  5. Frontend calls GET /api/auth/session to learn who is signed in and get the CSRF token
  6. POST /api/auth/logout (with X-CSRF-Token) revokes the session and clears the cookie
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.deps import DbSession
from app.config import Settings
from app.schemas.auth import AuthSessionOut
from app.schemas.common import error_responses
from app.security import (
    Principal,
    check_origin,
    clear_oauth_tx_cookie,
    clear_session_cookie,
    get_optional_principal,
    read_oauth_tx_cookie,
    set_oauth_tx_cookie,
    set_session_cookie,
    verify_csrf_header,
)
from app.services import auth as service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"], responses=error_responses(422, 500, 503))


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/login", status_code=status.HTTP_302_FOUND, summary="Start Google sign-in",
            response_class=RedirectResponse,
            responses={302: {"description": "Redirect to Google, or back to the frontend with ?auth_error="}})
def login(request: Request,
          return_to: Annotated[str | None, Query(max_length=2000,
                                                 description="Frontend URL to return to (must be on CORS_ORIGINS)")]
          = None):
    """Redirects the browser to Google's consent screen. Requests only `openid email profile`."""
    settings = _settings(request)
    google = request.app.state.google
    if not google.configured:
        target = service.with_query(service.safe_return_to(settings, return_to), auth_error="auth_not_configured")
        return RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    url, tx = service.begin_sign_in(google, settings, return_to)
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    set_oauth_tx_cookie(response, settings, tx)
    return response


@router.get("/callback", status_code=status.HTTP_303_SEE_OTHER, summary="Google OAuth callback",
            response_class=RedirectResponse,
            responses={303: {"description": "Back to the frontend with ?signin=success or ?auth_error=<code>"}})
def callback(request: Request, db: DbSession,
             code: Annotated[str | None, Query(max_length=2048)] = None,
             state: Annotated[str | None, Query(max_length=512)] = None,
             error: Annotated[str | None, Query(max_length=200)] = None):
    """Validates state, exchanges the code (with PKCE), verifies the ID token, creates or updates
    the user by Google `sub`, and sets the session cookie. Never returns tokens to the browser."""
    settings = _settings(request)
    tx = read_oauth_tx_cookie(request, settings)
    fallback = (tx or {}).get("return_to") or settings.frontend_url

    audit = request.app.state.audit
    if error:
        reason = "access_denied" if error == "access_denied" else "google_error"
        audit.record("auth.sign_in_failed", severity="warning", status="failed", metadata={"reason": reason, "source": "google"})
        response = RedirectResponse(service.with_query(fallback, auth_error=reason), status.HTTP_303_SEE_OTHER)
    else:
        try:
            result = service.complete_sign_in(db, request.app.state.google, settings,
                                              code=code, state=state, tx=tx)
        except service.SignInRejected as exc:
            log.warning("sign-in rejected: %s (%s)", exc.code, exc)
            audit.record("auth.sign_in_failed", severity="warning", status="failed", metadata={"reason": exc.code})
            response = RedirectResponse(service.with_query(fallback, auth_error=exc.code), status.HTTP_303_SEE_OTHER)
        else:
            response = RedirectResponse(service.with_query(result.return_to, signin="success"),
                                        status.HTTP_303_SEE_OTHER)
            set_session_cookie(response, settings, result.session_token)
            audit.record("auth.sign_in", user_id=result.user.user_id, status="ok",
                         metadata={"role": result.user.role, "method": "google"})
    clear_oauth_tx_cookie(response, settings)  # single use, success or not
    return response


@router.get("/session", response_model=AuthSessionOut, summary="Who am I?")
def session(request: Request, principal: Annotated[Principal | None, Depends(get_optional_principal)]):
    """Returns the signed-in user and the CSRF token, or `authenticated: false`. Always 200."""
    return service.describe(principal, _settings(request))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out",
             responses=error_responses(403))
def logout(request: Request, db: DbSession,
           principal: Annotated[Principal | None, Depends(get_optional_principal)]):
    """Revokes the server-side session and clears the cookie. Requires `X-CSRF-Token` when signed in.
    Idempotent: without a valid session it just clears the cookie."""
    settings = _settings(request)
    check_origin(request)
    if principal is not None:
        verify_csrf_header(request, principal)
    service.sign_out(db, settings, request.cookies.get(settings.session_cookie_name))
    if principal is not None:
        request.app.state.audit.record("auth.sign_out", user_id=principal.user_id, status="ok",
                                       metadata={"login_session_id": str(principal.login_session_id)})
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response, settings)
    return response
