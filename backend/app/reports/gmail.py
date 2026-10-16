"""Minimal Gmail/Google OAuth client for SENDING a summary to the user's own address.

Only `gmail.send` is used: no reading, listing or searching of mail. Network access is injectable for tests.
Tokens and message bodies are never logged.
"""
from __future__ import annotations

import base64
import logging
import time
from email.message import EmailMessage

import httpx

from app.reports.config import ReportSettings

log = logging.getLogger(__name__)


class GmailError(Exception):
    code = "gmail_error"
    retryable = False

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TokenRevoked(GmailError):
    """Google no longer honours the refresh token (user revoked access, token expired or changed password)."""
    code = "token_revoked"


class ScopeMissing(GmailError):
    code = "scope_missing"


class RateLimited(GmailError):
    code = "rate_limited"


class Transient(GmailError):
    code = "gmail_unavailable"
    retryable = True


class GmailClient:
    def __init__(self, settings: ReportSettings, client_id: str, client_secret: str, *,
                 http: httpx.Client | None = None, sleep=time.sleep):
        self._s = settings
        self._client_id, self._client_secret = client_id, client_secret
        self._http = http or httpx.Client(timeout=settings.report_http_timeout_seconds)
        self._sleep = sleep

    # ---- token handling
    def access_token(self, refresh_token: str) -> str:
        data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": self._client_id,
                "client_secret": self._client_secret}
        resp = self._post(self._s.google_token_url, data=data)
        if resp.status_code == 200:
            try:
                return resp.json()["access_token"]
            except (ValueError, KeyError):
                raise Transient("Google returned an unreadable token response.") from None
        error = _error_code(resp)
        if resp.status_code in (400, 401) and error in ("invalid_grant", "unauthorized_client", "invalid_client"):
            raise TokenRevoked("Google no longer accepts the saved permission. Enable email reports again.")
        if resp.status_code == 429:
            raise RateLimited("Google is rate limiting token requests.", retry_after=_retry_after(resp))
        raise Transient(f"Could not refresh Gmail access (HTTP {resp.status_code}).")

    def revoke(self, token: str) -> bool:
        """Revoke at Google. True when Google confirms or the token was already invalid."""
        try:
            resp = self._http.post(self._s.google_revoke_url, data={"token": token},
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        except httpx.HTTPError:
            return False
        return resp.status_code == 200 or (resp.status_code == 400 and _error_code(resp) == "invalid_token")

    # ---- sending
    def send(self, access_token: str, *, to: str, subject: str, body: str) -> str:
        msg = EmailMessage()
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        url = f"{self._s.gmail_api_base}/gmail/v1/users/me/messages/send"
        resp = self._post(url, json={"raw": raw}, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code == 200:
            try:
                return str(resp.json().get("id", ""))
            except ValueError:
                return ""
        error = _error_code(resp)
        if resp.status_code == 401:
            raise TokenRevoked("Gmail rejected the access token.")
        if resp.status_code == 403 and _is_scope_error(resp):
            raise ScopeMissing("The saved permission does not allow sending email. Enable email reports again.")
        if resp.status_code in (403, 429):                 # Gmail reports quota problems as 403 or 429
            raise RateLimited("Gmail is rate limiting this account. Try again later.", retry_after=_retry_after(resp))
        raise Transient(f"Gmail could not send the message (HTTP {resp.status_code}{' ' + error if error else ''}).")

    # ---- HTTP with bounded retries (5xx and network errors only)
    def _post(self, url: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(1, self._s.report_max_retries + 2):
            try:
                resp = self._http.post(url, **kw)
                if resp.status_code < 500:
                    return resp
                last = Transient(f"HTTP {resp.status_code}")
            except httpx.HTTPError as exc:
                last = Transient(f"network error ({type(exc).__name__})")
            if attempt <= self._s.report_max_retries:
                self._sleep(self._s.report_retry_backoff_seconds * 2 ** (attempt - 1))
        raise Transient(f"Google could not be reached ({last}).")


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        return str(err.get("status") or err.get("message") or "")[:60]
    return ""


def _is_scope_error(resp: httpx.Response) -> bool:
    text = resp.text.lower()
    return "insufficient" in text and ("scope" in text or "permission" in text)


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("retry-after")
    return float(v) if v and v.replace(".", "", 1).isdigit() else None
