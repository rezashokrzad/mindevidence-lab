"""Google OAuth 2.0 / OpenID Connect client (server-side authorization-code flow).

* Scopes are limited to `openid email profile`. No Gmail or other API access is requested.
* PKCE (S256) and a nonce are used on top of the client secret.
* ID tokens are verified with Google's `google-auth` library, which checks the RS256
  signature against Google's published certificates, the issuer (accounts.google.com),
  the audience (our client id), and expiry/issued-at.
* Network access goes through injectable transports so tests can run offline with
  locally signed tokens.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from google.auth import exceptions as google_exceptions
from google.auth.transport import Request as GoogleTransportRequest
from google.auth.transport.requests import Request as RequestsTransport
from google.oauth2 import id_token as google_id_token

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPES = ("openid", "email", "profile")
CLOCK_SKEW_SECONDS = 30


class GoogleAuthError(Exception):
    """Raised for any failure in the Google exchange; `code` is safe to show to users."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


class CachingCertsRequest(GoogleTransportRequest):
    """google-auth transport that caches successful GETs (Google's signing certs) for a while.

    google-auth fetches the certificate list on every verification; this avoids a network
    round trip per sign-in while still picking up key rotation within `ttl_seconds`.
    """

    def __init__(self, inner: GoogleTransportRequest | None = None, ttl_seconds: int = 3600):
        self._inner = inner or RequestsTransport()
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def __call__(self, url, method="GET", body=None, headers=None, timeout=10, **kwargs):
        if method != "GET":
            return self._inner(url, method=method, body=body, headers=headers, timeout=timeout, **kwargs)
        with self._lock:
            hit = self._cache.get(url)
            if hit and time.monotonic() - hit[0] < self._ttl:
                return hit[1]
        response = self._inner(url, method=method, body=body, headers=headers, timeout=timeout, **kwargs)
        if response.status == 200:
            with self._lock:
                self._cache[url] = (time.monotonic(), response)
        return response


class GoogleOIDCClient:
    def __init__(self, *, client_id: str, client_secret: str, redirect_uri: str,
                 http_client: httpx.Client | None = None,
                 certs_request: GoogleTransportRequest | None = None):
        self.client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._http = http_client or httpx.Client(timeout=10.0)
        self._certs_request = certs_request or CachingCertsRequest()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self._client_secret)

    def build_authorization_request(self, *, scopes: tuple[str, ...] = SCOPES, access_type: str = "online",
                                    prompt: str = "select_account", login_hint: str | None = None) -> AuthorizationRequest:
        """Sign-in uses the defaults (identity scopes only). The optional Gmail flow passes its own scopes,
        access_type=offline and prompt=consent, and never `include_granted_scopes`."""
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)  # 86 chars, within PKCE's 43–128
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "access_type": access_type,   # sign-in: online (no refresh token); Gmail reports: offline
            "prompt": prompt,
        }
        if login_hint:
            params["login_hint"] = login_hint
        return AuthorizationRequest(f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}", state, nonce, verifier)

    def exchange_code(self, code: str, code_verifier: str) -> str:
        """Exchange an authorization code for tokens and return the raw ID token.

        Sign-in discards the access token: it never calls Google APIs on the user's behalf.
        """
        try:
            return self.exchange_code_tokens(code, code_verifier)["id_token"]
        except KeyError:
            raise GoogleAuthError("token_exchange_failed", "token response had no id_token") from None

    def exchange_code_tokens(self, code: str, code_verifier: str) -> dict:
        """Exchange an authorization code and return the token response (callers must not log it)."""
        try:
            resp = self._http.post(TOKEN_ENDPOINT, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "code_verifier": code_verifier,
            }, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise GoogleAuthError("token_exchange_failed", f"token endpoint unreachable ({type(exc).__name__})") from None
        if resp.status_code != 200:
            # Google's error body names the problem (e.g. invalid_grant) without secrets.
            reason = ""
            try:
                reason = resp.json().get("error", "")
            except ValueError:
                pass
            raise GoogleAuthError("token_exchange_failed", f"token endpoint returned {resp.status_code} {reason}".strip())
        try:
            data = resp.json()
        except ValueError:
            raise GoogleAuthError("token_exchange_failed", "token response was not JSON") from None
        if not isinstance(data, dict):
            raise GoogleAuthError("token_exchange_failed", "token response was not an object")
        return data

    def verify_id_token(self, raw_token: str, *, expected_nonce: str) -> dict:
        """Verify signature, issuer, audience, and expiry (google-auth), then the nonce."""
        if not self.client_id:
            # google-auth skips the audience check when audience is None; never allow that.
            raise GoogleAuthError("auth_not_configured", "GOOGLE_CLIENT_ID is not set")
        try:
            claims = google_id_token.verify_oauth2_token(
                raw_token, self._certs_request, audience=self.client_id,
                clock_skew_in_seconds=CLOCK_SKEW_SECONDS)
        except (ValueError, google_exceptions.GoogleAuthError) as exc:
            raise GoogleAuthError("invalid_id_token", f"ID token rejected: {exc}") from None
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
            raise GoogleAuthError("invalid_id_token", "ID token nonce does not match this sign-in")
        if not claims.get("sub"):
            raise GoogleAuthError("invalid_id_token", "ID token has no subject")
        return claims

    def close(self) -> None:
        self._http.close()
