"""Google sign-in flow, session handling, sign-out, and route protection.

Google is replaced by tests/fake_google.py: real RS256 ID tokens are verified by
google-auth; only network calls are faked. The real Google endpoints are NOT contacted.
"""
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import text

from app.integrations.google import AUTHORIZATION_ENDPOINT, pkce_challenge
from tests.conftest import FRONTEND
from tests.fake_google import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI
from tests.test_api_patients import assert_error

pytestmark = pytest.mark.usefixtures("clean_chat")

SESSION_COOKIE = "me_session"


def query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def start_login(client, return_to=FRONTEND + "/"):
    r = client.get("/api/auth/login", params={"return_to": return_to}, follow_redirects=False)
    assert r.status_code == 302, r.text
    return r, query(r.headers["location"])


def callback(client, **params):
    return client.get("/api/auth/callback", params=params, follow_redirects=False)


def sign_in(client, fake_google, *, return_to=FRONTEND + "/", **claim_overrides):
    """Run the whole browser flow; returns the callback response."""
    _, auth = start_login(client, return_to)
    fake_google.respond_with_id_token(
        lambda form: fake_google.sign(fake_google.claims(nonce=auth["nonce"], **claim_overrides)))
    return callback(client, code="auth-code-123", state=auth["state"])


def user_rows(engine, sub):
    with engine.connect() as c:
        return c.execute(text("SELECT user_id, email, display_name, role FROM auth.users WHERE google_sub = :s"),
                         {"s": sub}).all()


def fresh_sub() -> str:
    return str(uuid.uuid4().int)[:21]


# ---------- unauthenticated access ----------

PROTECTED = [
    ("GET", "/api/patients"), ("GET", "/api/patients/P-001"), ("GET", "/api/patients/P-001/history"),
    ("GET", "/api/documents"), ("GET", "/api/documents/NIDA-PDAT"), ("GET", "/api/chunks/NIDA-PDAT-c0001"),
    ("GET", "/api/sessions"), ("POST", "/api/sessions"),
    ("GET", f"/api/sessions/{uuid.UUID(int=1)}/messages"),
]


@pytest.mark.parametrize("method, path", PROTECTED)
def test_protected_routes_reject_anonymous(anon_client, method, path):
    r = anon_client.request(method, path, json={"patient_id": "P-001"} if method == "POST" else None)
    assert_error(r, 401, "authentication_required")
    assert "items" not in r.json() and "presenting_concern" not in r.text  # no data leaks


@pytest.mark.parametrize("method, path", PROTECTED)
def test_protected_routes_reject_forged_cookie(anon_client, method, path):
    anon_client.cookies.set(SESSION_COOKIE, "forged-" + "a" * 40)
    r = anon_client.request(method, path, json={"patient_id": "P-001"} if method == "POST" else None)
    assert_error(r, 401, "authentication_required")


def test_public_routes_stay_open(anon_client):
    assert anon_client.get("/api/health").status_code == 200
    s = anon_client.get("/api/auth/session").json()
    assert s == {"authenticated": False, "user": None, "csrf_token": None, "expires_at": None,
                 "sign_in_available": True}


def test_signed_in_user_reaches_protected_routes(client):
    assert client.get("/api/patients").json()["total"] == 10
    assert client.get("/api/patients/P-001").status_code == 200
    assert client.get("/api/documents").status_code == 200
    assert client.get("/api/sessions").status_code == 200


# ---------- /api/auth/login ----------

def test_login_redirects_to_google_with_minimal_scopes_state_nonce_pkce(anon_client):
    r, q = start_login(anon_client)
    assert r.headers["location"].startswith(AUTHORIZATION_ENDPOINT + "?")
    assert q["client_id"] == CLIENT_ID
    assert q["redirect_uri"] == REDIRECT_URI
    assert q["response_type"] == "code"
    assert q["scope"] == "openid email profile"          # identity only
    assert "gmail" not in r.headers["location"].lower()
    assert "include_granted_scopes" not in q
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert len(q["state"]) >= 40 and len(q["nonce"]) >= 40 and q["state"] != q["nonce"]
    assert CLIENT_SECRET not in r.headers["location"]


def test_login_sets_signed_httponly_transaction_cookie(anon_client):
    r, q = start_login(anon_client)
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("me_oauth_tx=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/api/auth" in cookie
    assert "Max-Age=600" in cookie
    # Signed (itsdangerous: payload.timestamp.signature); tampering is tested separately.
    assert cookie.split(";")[0].count(".") >= 2
    # The PKCE verifier stays server-side: Google only ever sees the S256 challenge.
    assert "code_verifier" not in r.headers["location"]


def test_each_login_uses_fresh_state_and_nonce(anon_client):
    _, a = start_login(anon_client)
    _, b = start_login(anon_client)
    assert a["state"] != b["state"] and a["nonce"] != b["nonce"] and a["code_challenge"] != b["code_challenge"]


def test_login_without_google_config_redirects_with_error(make_client, fake_google):
    c = make_client(as_user=None, google=fake_google.client(client_id="", client_secret=""))
    r = c.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 302
    assert query(r.headers["location"])["auth_error"] == "auth_not_configured"
    assert r.headers["location"].startswith(FRONTEND)


# ---------- /api/auth/callback: success ----------

def test_full_sign_in_flow(anon_client, fake_google, engine):
    sub = fresh_sub()
    r = sign_in(anon_client, fake_google, return_to=FRONTEND + "/?tab=chat", sub=sub,
                email="Ada@Example.edu", name="Ada Lovelace")
    assert r.status_code == 303
    assert r.headers["location"] == FRONTEND + "/?tab=chat&signin=success"

    cookies = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith(SESSION_COOKIE + "="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie and "Path=/" in session_cookie
    assert "Max-Age=43200" in session_cookie  # SESSION_TTL_HOURS=12
    assert any(c.startswith("me_oauth_tx=") and "Max-Age=0" in c for c in cookies)  # tx cookie consumed

    # Token exchange: authorization code + PKCE verifier + secret, server to server.
    form = fake_google.token_requests[-1]
    assert form["grant_type"] == "authorization_code" and form["code"] == "auth-code-123"
    assert form["redirect_uri"] == REDIRECT_URI and form["client_secret"] == CLIENT_SECRET
    assert len(form["code_verifier"]) >= 43

    rows = user_rows(engine, sub)
    assert len(rows) == 1 and rows[0].email == "Ada@Example.edu" and rows[0].display_name == "Ada Lovelace"
    assert rows[0].role == "student"

    s = anon_client.get("/api/auth/session").json()
    assert s["authenticated"] is True and s["user"]["email"] == "Ada@Example.edu"
    assert s["user"]["role"] == "student" and len(s["csrf_token"]) >= 40
    assert anon_client.get("/api/patients").status_code == 200


def test_pkce_verifier_matches_challenge(anon_client, fake_google):
    _, auth = start_login(anon_client)
    fake_google.respond_with_id_token(lambda form: fake_google.sign(fake_google.claims(nonce=auth["nonce"])))
    callback(anon_client, code="c", state=auth["state"])
    assert pkce_challenge(fake_google.token_requests[-1]["code_verifier"]) == auth["code_challenge"]


def test_session_token_is_stored_only_as_hmac(anon_client, fake_google, engine):
    r = sign_in(anon_client, fake_google, sub=fresh_sub())
    token = anon_client.cookies.get(SESSION_COOKIE)
    with engine.connect() as c:
        hashes = c.execute(text("SELECT encode(token_hash, 'hex') FROM auth.login_sessions")).scalars().all()
    assert token and all(token not in h for h in hashes)
    assert r.status_code == 303


def test_user_identity_is_google_sub_not_email(make_client, fake_google, engine):
    sub = fresh_sub()
    c1 = make_client(as_user=None)
    sign_in(c1, fake_google, sub=sub, email="old@example.edu")
    c2 = make_client(as_user=None)
    sign_in(c2, fake_google, sub=sub, email="new@example.edu", name="Renamed")
    rows = user_rows(engine, sub)
    assert len(rows) == 1 and rows[0].email == "new@example.edu" and rows[0].display_name == "Renamed"

    # Same email, different sub → a different account, not a takeover.
    other_sub = fresh_sub()
    c3 = make_client(as_user=None)
    sign_in(c3, fake_google, sub=other_sub, email="new@example.edu")
    assert user_rows(engine, other_sub)[0].user_id != rows[0].user_id


def test_instructor_role_by_sub(make_client, fake_google, engine):
    sub = fresh_sub()
    c = make_client(as_user=None, instructor_google_subs=f"999,{sub}")
    sign_in(c, fake_google, sub=sub)
    assert c.get("/api/auth/session").json()["user"]["role"] == "instructor"


def test_return_to_is_restricted_to_frontend_origins(anon_client, fake_google):
    r = sign_in(anon_client, fake_google, sub=fresh_sub(), return_to="https://evil.example/phish")
    assert r.headers["location"] == FRONTEND + "/?signin=success"
    r2 = sign_in(anon_client, fake_google, sub=fresh_sub(), return_to="http://localhost:5173@evil.example/")
    assert urlsplit(r2.headers["location"]).netloc == "localhost:5173"


# ---------- /api/auth/callback: rejections ----------

def assert_rejected(r, code, anon_client):
    assert r.status_code == 303
    assert query(r.headers["location"])["auth_error"] == code
    assert not any(c.startswith(SESSION_COOKIE + "=") and "Max-Age=0" not in c
                   for c in r.headers.get_list("set-cookie"))
    assert anon_client.get("/api/auth/session").json()["authenticated"] is False
    assert_error(anon_client.get("/api/patients"), 401, "authentication_required")


def test_state_mismatch_rejected(anon_client, fake_google):
    _, auth = start_login(anon_client)
    fake_google.respond_with_id_token(lambda f: fake_google.sign(fake_google.claims(nonce=auth["nonce"])))
    r = callback(anon_client, code="c", state="attacker-chosen-state")
    assert_rejected(r, "invalid_state", anon_client)
    assert fake_google.token_requests == []  # nothing was exchanged


def test_callback_without_transaction_cookie_rejected(anon_client, fake_google):
    _, auth = start_login(anon_client)
    anon_client.cookies.clear()
    assert_rejected(callback(anon_client, code="c", state=auth["state"]), "invalid_state", anon_client)


def test_tampered_transaction_cookie_rejected(anon_client):
    _, auth = start_login(anon_client)
    raw = anon_client.cookies.get("me_oauth_tx")
    anon_client.cookies.clear()
    r = anon_client.get("/api/auth/callback", params={"code": "c", "state": auth["state"]},
                        headers={"Cookie": f"me_oauth_tx={raw[:-4]}AAAA"}, follow_redirects=False)
    assert_rejected(r, "invalid_state", anon_client)


def test_callback_cannot_be_replayed(anon_client, fake_google, engine):
    _, auth = start_login(anon_client)
    fake_google.respond_with_id_token(lambda f: fake_google.sign(fake_google.claims(nonce=auth["nonce"],
                                                                                    sub=fresh_sub())))
    assert "signin=success" in callback(anon_client, code="c", state=auth["state"]).headers["location"]
    again = callback(anon_client, code="c", state=auth["state"])
    assert query(again.headers["location"])["auth_error"] == "invalid_state"


def test_google_error_param(anon_client):
    start_login(anon_client)
    r = callback(anon_client, error="access_denied")
    assert_rejected(r, "access_denied", anon_client)


def test_token_exchange_failure(anon_client, fake_google):
    _, auth = start_login(anon_client)
    fake_google.token_response = None  # Google answers 400 invalid_grant
    assert_rejected(callback(anon_client, code="bad", state=auth["state"]), "token_exchange_failed", anon_client)


@pytest.mark.parametrize("case", ["wrong_audience", "wrong_issuer", "expired", "bad_signature", "wrong_nonce",
                                  "missing_nonce", "issued_in_future"])
def test_invalid_id_tokens_rejected(anon_client, fake_google, engine, case):
    sub = fresh_sub()
    _, auth = start_login(anon_client)
    import time
    now = int(time.time())
    overrides = {
        "wrong_audience": {"aud": "someone-else.apps.googleusercontent.com"},
        "wrong_issuer": {"iss": "https://evil.example"},
        "expired": {"iat": now - 7200, "exp": now - 3600},
        "bad_signature": {},
        "wrong_nonce": {"nonce": "a-different-nonce"},
        "missing_nonce": {"nonce": None},
        "issued_in_future": {"iat": now + 3600, "exp": now + 7200},
    }[case]
    claims = fake_google.claims(sub=sub, **{"nonce": auth["nonce"], **overrides})
    fake_google.respond_with_id_token(lambda f: fake_google.sign(claims, rogue=(case == "bad_signature")))
    assert_rejected(callback(anon_client, code="c", state=auth["state"]), "invalid_id_token", anon_client)
    assert user_rows(engine, sub) == []  # no account created from an invalid token


def test_unverified_email_rejected(anon_client, fake_google, engine):
    sub = fresh_sub()
    assert_rejected(sign_in(anon_client, fake_google, sub=sub, email_verified=False), "email_not_verified", anon_client)
    assert user_rows(engine, sub) == []


def test_allowlist_by_domain(make_client, fake_google, engine):
    c = make_client(as_user=None, allowed_email_domains="university.edu")
    outsider = fresh_sub()
    assert_rejected(sign_in(c, fake_google, sub=outsider, email="x@gmail.com"), "not_authorized", c)
    assert user_rows(engine, outsider) == []
    r = sign_in(c, fake_google, sub=fresh_sub(), email="student@university.edu")
    assert "signin=success" in r.headers["location"]


def test_allowlist_by_email(make_client, fake_google):
    c = make_client(as_user=None, allowed_emails="ta@gmail.com")
    assert "signin=success" in sign_in(c, fake_google, sub=fresh_sub(), email="TA@gmail.com").headers["location"]
    other = make_client(as_user=None, allowed_emails="ta@gmail.com")
    assert_rejected(sign_in(other, fake_google, sub=fresh_sub(), email="other@gmail.com"), "not_authorized", other)


def test_deactivated_user_cannot_sign_in(anon_client, fake_google, engine):
    sub = fresh_sub()
    sign_in(anon_client, fake_google, sub=sub)
    with engine.begin() as c:
        c.execute(text("UPDATE auth.users SET is_active = false WHERE google_sub = :s"), {"s": sub})
    # The existing session stops working immediately …
    assert_error(anon_client.get("/api/patients"), 401, "authentication_required")
    # … and signing in again is refused.
    anon_client.cookies.clear()
    assert_rejected(sign_in(anon_client, fake_google, sub=sub), "account_disabled", anon_client)


def test_production_cookies_are_secure_and_host_prefixed(make_client, fake_google):
    prod = make_client(as_user=None, app_env="production", cookie_secure=True,
                       cors_origins="https://lab.example.edu", frontend_url="https://lab.example.edu/",
                       google_redirect_uri="https://lab.example.edu/api/auth/callback")
    r, auth = start_login(prod, return_to="https://lab.example.edu/")
    assert "Secure" in r.headers["set-cookie"]
    tx = r.headers["set-cookie"].split(";")[0]
    fake_google.respond_with_id_token(lambda f: fake_google.sign(fake_google.claims(nonce=auth["nonce"],
                                                                                    sub=fresh_sub())))
    done = prod.get("/api/auth/callback", params={"code": "c", "state": auth["state"]},
                    headers={"Cookie": tx}, follow_redirects=False)
    session_cookie = next(c for c in done.headers.get_list("set-cookie") if c.startswith("__Host-me_session="))
    for flag in ("Secure", "HttpOnly", "Path=/", "SameSite=lax"):
        assert flag in session_cookie
    assert "Domain=" not in session_cookie
    assert done.headers["location"] == "https://lab.example.edu/?signin=success"


# ---------- session lifetime ----------

def test_expired_session_rejected(client, engine):
    with engine.begin() as c:
        c.execute(text("UPDATE auth.login_sessions SET created_at = now() - interval '2 days', "
                       "expires_at = now() - interval '1 second' "
                       "WHERE user_id = :u"), {"u": client.user_id})
    assert_error(client.get("/api/patients"), 401, "authentication_required")
    assert client.get("/api/auth/session").json()["authenticated"] is False


def test_google_certs_are_cached(anon_client, fake_google):
    from app.integrations.google import CachingCertsRequest

    cached = CachingCertsRequest(inner=fake_google.certs_request, ttl_seconds=60)
    for _ in range(3):
        cached("https://www.googleapis.com/oauth2/v1/certs")
    assert fake_google.cert_fetches == 1


# ---------- sign-out ----------

def test_sign_out_revokes_session_and_clears_cookie(client, engine):
    r = client.post("/api/auth/logout")
    assert r.status_code == 204
    cleared = next(c for c in r.headers.get_list("set-cookie") if c.startswith(SESSION_COOKIE + "="))
    assert "Max-Age=0" in cleared
    with engine.connect() as c:
        revoked = c.execute(text("SELECT count(*) FROM auth.login_sessions WHERE user_id = :u "
                                 "AND revoked_at IS NOT NULL"), {"u": client.user_id}).scalar_one()
    assert revoked == 1


def test_old_cookie_is_useless_after_sign_out(client):
    token = client.cookies.get(SESSION_COOKIE)
    client.post("/api/auth/logout")
    client.cookies.set(SESSION_COOKIE, token)  # replay the stolen/old cookie
    assert_error(client.get("/api/patients"), 401, "authentication_required")


def test_sign_out_requires_csrf_token(client):
    assert_error(client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong" * 10}), 403, "csrf_failed")
    assert client.get("/api/patients").status_code == 200  # still signed in


def test_sign_out_rejects_foreign_origin(client):
    assert_error(client.post("/api/auth/logout", headers={"Origin": "https://evil.example"}), 403, "csrf_failed")
    assert client.get("/api/patients").status_code == 200


def test_sign_out_when_signed_out_is_harmless(anon_client):
    assert anon_client.post("/api/auth/logout").status_code == 204


def test_sign_out_only_ends_this_device(make_client, make_user):
    user = make_user()
    laptop, phone = make_client(as_user=user), make_client(as_user=user)
    laptop.post("/api/auth/logout")
    assert_error(laptop.get("/api/patients"), 401, "authentication_required")
    assert phone.get("/api/patients").status_code == 200
