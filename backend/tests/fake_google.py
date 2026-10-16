"""An offline stand-in for Google's OIDC endpoints.

ID tokens are real RS256 JWTs signed with a locally generated key; the matching
self-signed X.509 certificate is served to google-auth through a fake transport,
so `google.oauth2.id_token.verify_oauth2_token` performs its full signature,
issuer, audience, and expiry checks. Only the network is faked.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from urllib.parse import parse_qs

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt

from app.integrations.google import TOKEN_ENDPOINT, GoogleOIDCClient

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret-not-real"
REDIRECT_URI = "http://localhost:8000/api/auth/callback"
CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"


def _keypair(kid: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, kid)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return pem_key, cert.public_bytes(serialization.Encoding.PEM).decode()


class _Resp:
    def __init__(self, status: int, data: bytes):
        self.status = status
        self.data = data
        self.headers = {"content-type": "application/json"}


class FakeGoogle:
    def __init__(self):
        self.kid = "test-kid-1"
        self._key_pem, cert_pem = _keypair(self.kid)
        self._rogue_key_pem, _ = _keypair("rogue")   # a key Google never published
        self.certs = {self.kid: cert_pem}
        self.cert_fetches = 0
        self.token_requests: list[dict] = []
        # What the next token-endpoint call returns: a callable(form) -> httpx.Response
        self.token_response = None

    # ---- tokens ----
    def claims(self, **overrides) -> dict:
        now = int(time.time())
        base = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "azp": CLIENT_ID,
                "sub": "100000000000000000001", "email": "student@example.edu", "email_verified": True,
                "name": "Test Student", "iat": now, "exp": now + 3600, "nonce": "set-me"}
        base.update(overrides)
        return {k: v for k, v in base.items() if v is not None}

    def sign(self, claims: dict, *, rogue: bool = False) -> str:
        signer = crypt.RSASigner.from_string(self._rogue_key_pem if rogue else self._key_pem, key_id=self.kid)
        return jwt.encode(signer, claims).decode()

    # ---- transports ----
    def certs_request(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        assert url == CERTS_URL, url
        self.cert_fetches += 1
        return _Resp(200, json.dumps(self.certs).encode())

    def _token_handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TOKEN_ENDPOINT
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        if self.token_response is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return self.token_response(form)

    def client(self, *, client_id: str = CLIENT_ID, client_secret: str = CLIENT_SECRET,
               redirect_uri: str = REDIRECT_URI) -> GoogleOIDCClient:
        return GoogleOIDCClient(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri,
                                http_client=httpx.Client(transport=httpx.MockTransport(self._token_handler)),
                                certs_request=self.certs_request)

    def respond_with_tokens(self, make_token, *, scope: str, refresh_token: str | None = "1//refresh-NOT-REAL",
                            access_token: str = "ya29.access-NOT-REAL"):
        """A token response like Google's for the optional Gmail consent (has scope and a refresh token)."""
        def make(form):
            body = {"access_token": access_token, "expires_in": 3599, "token_type": "Bearer", "scope": scope,
                    "id_token": make_token(form)}
            if refresh_token:
                body["refresh_token"] = refresh_token
            return httpx.Response(200, json=body)
        self.token_response = make

    def respond_with_id_token(self, make_token):
        """make_token(form) -> raw ID token string; wrapped in a 200 token response."""
        self.token_response = lambda form: httpx.Response(200, json={
            "access_token": "ya29.not-used", "expires_in": 3599, "token_type": "Bearer",
            "scope": "openid email profile", "id_token": make_token(form)})
