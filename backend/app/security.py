"""Authentication and CSRF primitives.

Session model
-------------
* After Google sign-in, the server creates a row in auth.login_sessions and sets a cookie
  holding a random 256-bit token: HttpOnly, SameSite=Lax, Path=/, Secure when COOKIE_SECURE.
* Only HMAC-SHA256(SESSION_SECRET, token) is stored, so a database leak does not yield
  usable cookies. Sign-out revokes the row server-side and clears the cookie.

CSRF protection for state-changing requests (POST)
--------------------------------------------------
1. SameSite=Lax: browsers do not send the cookie on cross-site POSTs.
2. Origin check: if the browser sends Origin, it must be one of CORS_ORIGINS (or the API's own origin).
3. Synchronizer token: the X-CSRF-Token header must equal the token bound to the login session,
   obtained from GET /api/auth/session. Custom headers also force a CORS preflight, which only
   CORS_ORIGINS pass.

OAuth sign-in CSRF/replay protection: random `state` + `nonce` + PKCE verifier are kept in a
short-lived (10 min), signed, HttpOnly cookie scoped to /api/auth and checked on the callback.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.errors import AuthenticationRequired, CsrfFailed, Forbidden
from app.repositories import users as user_repo

OAUTH_TX_COOKIE = "me_oauth_tx"
OAUTH_TX_MAX_AGE = 600
OAUTH_TX_PATH = "/api/auth"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_TOUCH_INTERVAL_SECONDS = 300


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    display_name: str | None
    role: Literal["student", "instructor"]
    login_session_id: uuid.UUID
    csrf_token: str
    expires_at: datetime


# ---------- tokens ----------

def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(settings: Settings, token: str) -> bytes:
    return hmac.new(settings.session_secret.get_secret_value().encode(), token.encode(), hashlib.sha256).digest()


# ---------- cookies ----------

def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name, token,
        max_age=settings.session_ttl_hours * 3600, path="/",
        httponly=True, secure=settings.cookie_secure, samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/", httponly=True,
                           secure=settings.cookie_secure, samesite="lax")


def _tx_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="mindevidence-oauth-tx")


def set_oauth_tx_cookie(response: Response, settings: Settings, payload: dict) -> None:
    response.set_cookie(
        OAUTH_TX_COOKIE, _tx_serializer(settings).dumps(payload),
        max_age=OAUTH_TX_MAX_AGE, path=OAUTH_TX_PATH,
        httponly=True, secure=settings.cookie_secure, samesite="lax",
    )


def clear_oauth_tx_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(OAUTH_TX_COOKIE, path=OAUTH_TX_PATH, httponly=True,
                           secure=settings.cookie_secure, samesite="lax")


def read_oauth_tx_cookie(request: Request, settings: Settings) -> dict | None:
    raw = request.cookies.get(OAUTH_TX_COOKIE)
    if not raw:
        return None
    try:
        data = _tx_serializer(settings).loads(raw, max_age=OAUTH_TX_MAX_AGE)
    except BadSignature:  # includes SignatureExpired
        return None
    return data if isinstance(data, dict) else None


# ---------- principal ----------

def resolve_principal(request: Request, db: Session) -> Principal | None:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    if not token or len(token) > 128:
        return None
    row = user_repo.find_active_session(db, hash_session_token(settings, token))
    if row is None:
        return None
    # Record activity at most every few minutes to avoid a write per request.
    if (datetime.now(timezone.utc) - row.last_seen_at).total_seconds() > _TOUCH_INTERVAL_SECONDS:
        user_repo.touch_session(db, row.login_session_id)
        db.commit()
    u = row.user
    return Principal(user_id=u.user_id, email=u.email, display_name=u.display_name, role=u.role,
                     login_session_id=row.login_session_id, csrf_token=row.csrf_token,
                     expires_at=row.expires_at)


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Dependency for protected routes: 401 unless a valid, unexpired, unrevoked session exists."""
    principal = resolve_principal(request, db)
    if principal is None:
        raise AuthenticationRequired("Sign in with Google to use this endpoint.")
    return principal


def get_optional_principal(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    return resolve_principal(request, db)


# ---------- CSRF ----------

def check_origin(request: Request) -> None:
    """Reject unsafe requests whose Origin (or Sec-Fetch-Site) shows a foreign site."""
    if request.method in SAFE_METHODS:
        return
    settings: Settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin:
        own = f"{request.url.scheme}://{request.url.netloc}"
        if origin.rstrip("/") not in settings.cors_origin_list and origin != own:
            raise CsrfFailed("Request origin is not allowed.")
    elif request.headers.get("sec-fetch-site") == "cross-site":
        raise CsrfFailed("Cross-site request blocked.")


def require_csrf(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
    """Dependency for authenticated state-changing routes: Origin check + synchronizer token."""
    check_origin(request)
    verify_csrf_header(request, principal)
    return principal


def verify_csrf_header(request: Request, principal: Principal) -> None:
    sent = request.headers.get(CSRF_HEADER, "")
    if not sent or not hmac.compare_digest(sent, principal.csrf_token):
        raise CsrfFailed("Missing or invalid CSRF token. Send the X-CSRF-Token from GET /api/auth/session.")


# ---------- roles ----------

def require_instructor(principal: Principal = Depends(get_principal)) -> Principal:
    """Administrative reads: signed in AND instructor role (403 otherwise)."""
    if principal.role != "instructor":
        raise Forbidden("This endpoint is restricted to instructors.")
    return principal


def require_instructor_csrf(request: Request, principal: Principal = Depends(require_csrf)) -> Principal:
    """Administrative writes: instructor role + Origin check + CSRF token."""
    if principal.role != "instructor":
        raise Forbidden("This endpoint is restricted to instructors.")
    return principal


# ---------- a second signed transaction cookie (optional Gmail consent flow) ----------

GMAIL_TX_COOKIE = "me_gmail_tx"
GMAIL_TX_PATH = "/api/reports"


def _gmail_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="mindevidence-gmail-tx")


def set_gmail_tx_cookie(response: Response, settings: Settings, payload: dict) -> None:
    response.set_cookie(GMAIL_TX_COOKIE, _gmail_serializer(settings).dumps(payload), max_age=OAUTH_TX_MAX_AGE,
                        path=GMAIL_TX_PATH, httponly=True, secure=settings.cookie_secure, samesite="lax")


def clear_gmail_tx_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(GMAIL_TX_COOKIE, path=GMAIL_TX_PATH, httponly=True, secure=settings.cookie_secure,
                           samesite="lax")


def read_gmail_tx_cookie(request: Request, settings: Settings) -> dict | None:
    raw = request.cookies.get(GMAIL_TX_COOKIE)
    if not raw:
        return None
    try:
        data = _gmail_serializer(settings).loads(raw, max_age=OAUTH_TX_MAX_AGE)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None
