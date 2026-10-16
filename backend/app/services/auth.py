"""Sign-in use cases: start the Google flow, complete it, inspect and end sessions."""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.google import GoogleAuthError, GoogleOIDCClient
from app.models import User
from app.repositories import users as repo
from app.schemas.auth import AuthSessionOut, UserOut
from app.security import Principal, hash_session_token, new_csrf_token, new_session_token

log = logging.getLogger(__name__)


class SignInRejected(Exception):
    """Sign-in failed; `code` is a short, user-safe reason sent back to the frontend."""

    def __init__(self, code: str, reason: str = ""):
        super().__init__(reason or code)
        self.code = code


@dataclass(frozen=True)
class SignInResult:
    user: User
    session_token: str
    return_to: str


# ---------- redirects ----------

def safe_return_to(settings: Settings, value: str | None) -> str:
    """Only allow redirects back to a configured frontend origin (prevents open redirects)."""
    if value:
        parts = urlsplit(value)
        origin = f"{parts.scheme}://{parts.netloc}"
        if (parts.scheme in ("http", "https") and "@" not in parts.netloc
                and origin in settings.cors_origin_list):
            return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
    return settings.frontend_url


def with_query(url: str, **params: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in params and k not in ("auth_error", "signin")]
    query.extend(params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


# ---------- sign-in ----------

def begin_sign_in(google: GoogleOIDCClient, settings: Settings, return_to: str | None) -> tuple[str, dict]:
    """Return the Google authorization URL and the transaction to store in the signed cookie."""
    req = google.build_authorization_request()
    tx = {"state": req.state, "nonce": req.nonce, "verifier": req.code_verifier,
          "return_to": safe_return_to(settings, return_to)}
    return req.url, tx


def _authorized(settings: Settings, email: str) -> bool:
    domains, emails = settings.allowed_domain_list, settings.allowed_email_list
    if not domains and not emails:
        return True
    email = email.lower()
    return email in emails or email.rsplit("@", 1)[-1] in domains


def complete_sign_in(db: Session, google: GoogleOIDCClient, settings: Settings, *,
                     code: str | None, state: str | None, tx: dict | None) -> SignInResult:
    if tx is None or not state or not isinstance(tx.get("state"), str) \
            or not secrets.compare_digest(state, tx["state"]):
        raise SignInRejected("invalid_state", "state missing, expired, or mismatched")
    if not code:
        raise SignInRejected("invalid_request", "callback without authorization code")

    try:
        raw = google.exchange_code(code, tx["verifier"])
        claims = google.verify_id_token(raw, expected_nonce=tx["nonce"])
    except GoogleAuthError as exc:
        raise SignInRejected(exc.code, str(exc)) from None

    email = claims.get("email")
    if not email or claims.get("email_verified") is not True:
        raise SignInRejected("email_not_verified", "Google account email is missing or unverified")
    if not _authorized(settings, email):
        raise SignInRejected("not_authorized", "account is not on the allowed list")

    sub = str(claims["sub"])
    role = "instructor" if sub in settings.instructor_sub_list else "student"
    now = datetime.now(timezone.utc)
    user = repo.upsert_google_user(db, google_sub=sub, email=email, email_verified=True,
                                   display_name=(claims.get("name") or "").strip() or None, role=role, now=now)
    if not user.is_active:
        db.rollback()
        raise SignInRejected("account_disabled", "user is deactivated")

    repo.purge_stale_sessions(db, user.user_id)
    token = create_login_session(db, settings, user.user_id)
    db.commit()
    log.info("sign-in ok user_id=%s role=%s", user.user_id, user.role)
    return SignInResult(user=user, session_token=token, return_to=tx.get("return_to") or settings.frontend_url)


def create_login_session(db: Session, settings: Settings, user_id) -> str:
    """Create a server-side session and return the raw cookie token (stored only as an HMAC)."""
    token = new_session_token()
    repo.create_login_session(
        db, user_id=user_id, token_hash=hash_session_token(settings, token), csrf_token=new_csrf_token(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours))
    return token


# ---------- inspect / sign out ----------

def describe(principal: Principal | None, settings: Settings) -> AuthSessionOut:
    if principal is None:
        return AuthSessionOut(authenticated=False, user=None, csrf_token=None, expires_at=None,
                              sign_in_available=settings.google_configured)
    return AuthSessionOut(
        authenticated=True,
        user=UserOut(user_id=principal.user_id, email=principal.email, display_name=principal.display_name,
                     role=principal.role),
        csrf_token=principal.csrf_token,
        expires_at=principal.expires_at,
        sign_in_available=settings.google_configured,
    )


def sign_out(db: Session, settings: Settings, token: str | None) -> None:
    if token:
        repo.revoke_session(db, hash_session_token(settings, token))
        db.commit()
