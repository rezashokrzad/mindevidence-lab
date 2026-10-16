"""Optional Gmail activity reports: consent, token protection, minimal content, failures, withdrawal.

Google and Gmail are replaced by offline fakes (tests/fake_google*.py). A real consent screen and a real send were
NOT exercised by these tests."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.audit import AuditLog
from app.reports.config import GMAIL_FLOW_SCOPES, GMAIL_SEND_SCOPE, ReportSettings
from app.reports.gmail import GmailClient
from app.reports.service import ReportService, build_summary, render_summary
from app.reports.vault import TokenVault, VaultError
from tests.fake_google import CLIENT_ID, CLIENT_SECRET
from tests.fake_google_apis import ACCESS_TOKEN, FakeGoogleApis, error, settings as report_settings
from tests.test_api_patients import assert_error

FULL_SCOPE = f"openid https://www.googleapis.com/auth/userinfo.email {GMAIL_SEND_SCOPE}"
BASE = "/api/reports/email"


# ================================================================ unit: vault, config, summary text

def test_vault_round_trip_and_key_binding():
    key = Fernet.generate_key().decode()
    v = TokenVault(key)
    blob = v.encrypt("1//refresh-secret")
    assert b"1//refresh-secret" not in blob and v.decrypt(blob, v.key_id) == "1//refresh-secret"
    other = TokenVault(Fernet.generate_key().decode())
    with pytest.raises(VaultError, match="different key"):
        other.decrypt(blob, v.key_id)                                  # key changed -> clear error, not a crash
    with pytest.raises(VaultError, match="different key"):
        v.decrypt(blob, "wrong-key-id")
    with pytest.raises(VaultError):
        TokenVault.__new__(TokenVault) and TokenVault("")


def test_invalid_encryption_key_is_refused_at_startup_without_echoing_it():
    with pytest.raises(Exception) as exc:
        ReportSettings(email_token_encryption_key="not-a-valid-key-123")
    assert "not-a-valid-key-123" not in str(exc.value) and "Fernet" in str(exc.value)


def test_only_the_send_scope_is_requested_for_gmail():
    assert GMAIL_SEND_SCOPE == "https://www.googleapis.com/auth/gmail.send"
    assert set(GMAIL_FLOW_SCOPES) == {"openid", "email", GMAIL_SEND_SCOPE}
    assert not any(s.endswith(("gmail.readonly", "gmail.modify", "gmail.compose", "mail.google.com/")) for s in GMAIL_FLOW_SCOPES)


def test_rendered_summary_is_counts_only():
    s = {"period_days": 7, "questions_asked": 4, "answers": {"answered": 2, "refused": 1, "unsupported": 0, "no_evidence": 1,
         "urgent_escalations": 0, "errors": 0}, "research_searches": 3, "answers_with_unsupported_claims": 1,
         "median_answer_seconds": 4.2, "distinct_cases_worked_with": 2, "feedback": {"helpful": 1, "not_helpful": 0}}
    body = render_summary(s)
    assert "Questions asked: 4" in body and "does not include your questions" in body and "P-0" not in body


# ================================================================ integration



@pytest.fixture
def apis():
    return FakeGoogleApis()


@pytest.fixture
def make_reports(engine, fake_google, apis):
    def _make(**kw):
        rs = report_settings(**kw)
        svc = ReportService(
            settings=rs, vault=TokenVault(rs.email_token_encryption_key.get_secret_value()),
            gmail=GmailClient(rs, CLIENT_ID, CLIENT_SECRET, http=apis.http(), sleep=lambda s: None),
            google=fake_google.client(redirect_uri=rs.gmail_redirect_uri), audit=AuditLog(sessionmaker(bind=engine)))
        svc.rs = rs
        return svc
    return _make


@pytest.fixture
def user_client(make_client, make_reports):
    def _make(**kw):
        svc = make_reports(**kw)
        client = make_client(report_service=svc)
        client.svc = svc
        return client
    return _make


@pytest.fixture(autouse=True)
def _db_features_present(request):
    if "engine" in request.fixturenames or "user_client" in request.fixturenames or "make_client" in request.fixturenames:
        request.getfixturevalue("needs_vector")


def sub_of(engine, user_id) -> str:
    with engine.connect() as c:
        return c.execute(text("SELECT google_sub FROM auth.users WHERE user_id = :u"), {"u": user_id}).scalar_one()


def consent(engine, user_id):
    with engine.connect() as c:
        return c.execute(text("SELECT * FROM auth.email_report_consents WHERE user_id = :u"), {"u": user_id}).mappings().first()


def audit_types(engine, user_id):
    with engine.connect() as c:
        return c.execute(text("SELECT event_type FROM audit.events WHERE user_id = :u ORDER BY event_seq"),
                         {"u": user_id}).scalars().all()


def start_enable(client):
    r = client.post(f"{BASE}/enable")
    assert r.status_code == 200, r.text
    url = r.json()["authorization_url"]
    return url, {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}, r


def finish_enable(client, fake_google, engine, *, scope=FULL_SCOPE, refresh="1//refresh-NOT-REAL", sub=None, state=None,
                  error_param=None):
    url, q, _ = start_enable(client)
    sub = sub or sub_of(engine, client.user_id)
    fake_google.respond_with_tokens(lambda f: fake_google.sign(fake_google.claims(nonce=q["nonce"], sub=sub)),
                                    scope=scope, refresh_token=refresh)
    params = {"error": error_param} if error_param else {"code": "gmail-code-1", "state": state or q["state"]}
    return client.get(f"{BASE}/callback", params=params, follow_redirects=False)


def enable_ok(client, fake_google, engine, **kw):
    r = finish_enable(client, fake_google, engine, **kw)
    assert "email=enabled" in r.headers["location"], r.headers["location"]


# ---------------------------------------------------------------- availability and status

def test_feature_is_off_without_a_key_and_the_app_keeps_working(make_client):
    c = make_client()
    s = c.get(f"{BASE}/status").json()
    assert s["available"] is False and s["enabled"] is False and "encryption key" in s["unavailable_reason"]
    assert_error(c.post(f"{BASE}/enable"), 503, "email_reports_unavailable")
    assert_error(c.post(f"{BASE}/send"), 503, "email_reports_unavailable")
    assert c.get("/api/patients").status_code == 200 and c.post(f"{BASE}/disable").json()["enabled"] is False


def test_status_describes_exactly_what_is_and_is_not_sent(user_client):
    s = user_client().get(f"{BASE}/status").json()
    assert s["available"] and s["status"] == "never_enabled" and set(s["requested_scopes"]) == set(GMAIL_FLOW_SCOPES)
    assert any("not" in x.lower() or "never" in x.lower() for x in s["not_sent"]) and s["recipient"].count("*") >= 1
    assert "@" in s["recipient"] and s["limits"]["max_per_day"] == 3


def test_reports_need_sign_in_and_csrf(make_client, user_client):
    anon = make_client(as_user=None)
    for method, path in (("get", "/status"), ("post", "/enable"), ("post", "/disable"), ("post", "/send")):
        assert_error(getattr(anon, method)(BASE + path), 401, "authentication_required")
    c = user_client()
    for path in ("/enable", "/disable", "/send"):
        assert_error(c.post(BASE + path, headers={"X-CSRF-Token": "bad" * 20}), 403, "csrf_failed")


# ---------------------------------------------------------------- consent (incremental authorization)

def test_enable_builds_a_minimal_consent_request(user_client):
    c = user_client()
    url, q, r = start_enable(c)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert set(q["scope"].split()) == set(GMAIL_FLOW_SCOPES)
    assert q["access_type"] == "offline" and q["prompt"] == "consent" and q["response_type"] == "code"
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"].endswith("/api/reports/email/callback")
    assert "include_granted_scopes" not in q and "login_hint" in q
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("me_gmail_tx=") and "HttpOnly" in cookie and "Path=/api/reports" in cookie


def test_consent_stores_only_an_encrypted_refresh_token(user_client, fake_google, engine):
    c = user_client()
    r = finish_enable(c, fake_google, engine, refresh="1//the-real-refresh-token-value")
    assert r.status_code == 303 and "email=enabled" in r.headers["location"]
    row = consent(engine, c.user_id)
    assert row["status"] == "enabled" and row["granted_scope"] == GMAIL_SEND_SCOPE and row["token_key_id"]
    assert b"the-real-refresh-token-value" not in bytes(row["encrypted_refresh_token"])
    assert c.svc.vault.decrypt(row["encrypted_refresh_token"], row["token_key_id"]) == "1//the-real-refresh-token-value"
    assert "1//the-real-refresh-token-value" not in json.dumps(c.get(f"{BASE}/status").json(), default=str)
    assert {"email.consent_requested", "email.consent_granted"} <= set(audit_types(engine, c.user_id))
    assert c.get(f"{BASE}/status").json()["enabled"] is True


@pytest.mark.parametrize("case, reason", [
    ("bad_state", "invalid_state"), ("denied", "access_denied"), ("no_gmail_scope", "scope_not_granted"),
    ("other_account", "account_mismatch"), ("no_refresh", "no_refresh_token"),
])
def test_consent_failures_store_nothing(user_client, fake_google, engine, apis, case, reason):
    c = user_client()
    kw = {"bad_state": {"state": "attacker-state"}, "denied": {"error_param": "access_denied"},
          "no_gmail_scope": {"scope": "openid email"}, "other_account": {"sub": "999999999999999999999"},
          "no_refresh": {"refresh": None}}[case]
    r = finish_enable(c, fake_google, engine, **kw)
    assert f"email_error={reason}" in r.headers["location"]
    row = consent(engine, c.user_id)
    assert row is None or (row["status"] != "enabled" and row["encrypted_refresh_token"] is None)
    assert "email.consent_failed" in audit_types(engine, c.user_id)
    if case in ("no_gmail_scope", "other_account"):          # a token we do not want is revoked at Google right away
        assert len(apis.revoke_calls) == 1


def test_consent_callback_cannot_be_completed_by_another_user(user_client, make_client, fake_google, engine, make_reports):
    victim = user_client()
    url, q, _ = start_enable(victim)
    tx_cookie = victim.cookies.get("me_gmail_tx")
    attacker = make_client(report_service=victim.svc)
    fake_google.respond_with_tokens(lambda f: fake_google.sign(fake_google.claims(nonce=q["nonce"], sub=sub_of(engine, attacker.user_id))),
                                    scope=FULL_SCOPE)
    attacker.cookies.set("me_gmail_tx", tx_cookie, path="/api/reports")
    r = attacker.get(f"{BASE}/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert "email_error=invalid_state" in r.headers["location"]
    assert consent(engine, attacker.user_id) is None and consent(engine, victim.user_id) is None


def test_callback_without_sign_in_is_rejected(user_client, make_client, fake_google, engine):
    c = user_client()
    _, q, _ = start_enable(c)
    anon = make_client(as_user=None, report_service=c.svc)
    assert "email_error=not_signed_in" in anon.get(f"{BASE}/callback", params={"code": "c", "state": q["state"]},
                                                  follow_redirects=False).headers["location"]


# ---------------------------------------------------------------- sending

def audit_for(engine, user_id, question="Why is detox not enough?", patient="P-007", n=2):
    """Insert the kind of events a real session produces, with identifiable content that must never be emailed."""
    log = AuditLog(sessionmaker(bind=engine))
    for i in range(n):
        cid = f"cid-rep{i}"
        log.record("question.submitted", user_id=user_id, correlation_id=cid, patient_id=patient,
                   metadata={"mode": "rag", "question_length": len(question)})
        log.record("retrieval.executed", user_id=user_id, correlation_id=cid, metadata={"chunk_ids": ["NIDA-PDAT-c0004"]})
        log.record("answer.completed", user_id=user_id, correlation_id=cid, latency_ms=4200 + i * 100, patient_id=patient,
                   metadata={"outcome": "answered" if i == 0 else "refused", "unsupported_claims": i})


def test_send_delivers_a_counts_only_summary_to_the_users_own_address(user_client, fake_google, engine, apis):
    c = user_client()
    audit_for(engine, c.user_id)
    enable_ok(c, fake_google, engine)
    r = c.post(f"{BASE}/send")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["sent"] is True and "@" in out["recipient"] and out["summary"]["questions_asked"] == 2
    assert out["summary"]["answers"]["answered"] == 1 and out["summary"]["answers"]["refused"] == 1
    assert out["summary"]["distinct_cases_worked_with"] == 1 and out["summary"]["answers_with_unsupported_claims"] == 1

    sent = apis.send_calls[0]
    with engine.connect() as conn:
        own_email = conn.execute(text("SELECT email FROM auth.users WHERE user_id = :u"), {"u": c.user_id}).scalar_one()
    assert sent["to"] == own_email and sent["authorization"] == f"Bearer {ACCESS_TOKEN}" and sent["keys"] == ["raw"]
    assert "Questions asked: 2" in sent["body"] and "counts only" in sent["body"]
    for forbidden in ("P-007", "Why is detox", "cid-rep", "NIDA-PDAT", "chunk", own_email, "refresh"):
        assert forbidden not in sent["body"] and forbidden not in sent["subject"], forbidden
    assert len(apis.token_calls) == 1 and apis.token_calls[0]["grant_type"] == "refresh_token"
    assert consent(engine, c.user_id)["last_sent_at"] is not None and consent(engine, c.user_id)["consecutive_failures"] == 0
    types = audit_types(engine, c.user_id)
    assert {"email.report_attempted", "email.report_sent"} <= set(types)


def test_only_the_send_endpoint_is_ever_called_on_gmail(user_client, fake_google, engine, apis):
    c = user_client()
    enable_ok(c, fake_google, engine)
    c.post(f"{BASE}/send")
    c.post(f"{BASE}/disable")
    assert [x["url"].rsplit("/gmail/", 1)[-1] for x in apis.send_calls] == ["v1/users/me/messages/send"]   # never list/read


def test_send_without_consent_is_refused_and_calls_nothing(user_client, engine, apis):
    c = user_client()
    assert_error(c.post(f"{BASE}/send"), 409, "email_reports_not_enabled")
    assert apis.token_calls == [] and apis.send_calls == []


def test_send_is_rate_limited_per_interval_and_per_day(user_client, fake_google, engine, apis):
    c = user_client(report_min_interval_seconds=3600)
    enable_ok(c, fake_google, engine)
    assert c.post(f"{BASE}/send").status_code == 200
    r = c.post(f"{BASE}/send")
    err = assert_error(r, 429, "report_rate_limited")
    assert err["details"][0]["message"].startswith("retry_after_seconds=") and len(apis.send_calls) == 1

    d = user_client(report_max_per_day=1)
    enable_ok(d, fake_google, engine)
    assert d.post(f"{BASE}/send").status_code == 200
    assert_error(d.post(f"{BASE}/send"), 429, "report_daily_limit")
    assert len(apis.send_calls) == 2


def test_revoked_token_is_detected_and_consent_is_closed(user_client, fake_google, engine, apis):
    c = user_client()
    enable_ok(c, fake_google, engine)
    apis.token_behavior = [error(400, {"error": "invalid_grant", "error_description": "Token has been revoked."})]
    assert_error(c.post(f"{BASE}/send"), 409, "consent_revoked")
    row = consent(engine, c.user_id)
    assert row["status"] == "revoked" and row["encrypted_refresh_token"] is None and row["last_status"] == "token_revoked"
    assert {"email.report_failed", "email.consent_revoked"} <= set(audit_types(engine, c.user_id))
    assert apis.send_calls == []
    assert_error(c.post(f"{BASE}/send"), 409, "email_reports_not_enabled")        # nothing to retry with
    assert c.get(f"{BASE}/status").json()["status"] == "revoked"
    assert c.get("/api/patients").status_code == 200                              # the app is unaffected


def test_gmail_rejecting_the_access_token_or_scope_closes_consent(user_client, fake_google, engine, apis):
    c = user_client()
    enable_ok(c, fake_google, engine)
    apis.send_behavior = [error(403, {"error": {"status": "PERMISSION_DENIED", "message": "Request had insufficient authentication scopes."}})]
    assert_error(c.post(f"{BASE}/send"), 409, "consent_revoked")
    assert consent(engine, c.user_id)["encrypted_refresh_token"] is None


def test_gmail_quota_errors_are_reported_with_retry_after(user_client, fake_google, engine, apis):
    c = user_client()
    enable_ok(c, fake_google, engine)
    apis.send_behavior = [error(429, headers={"retry-after": "120"})]
    err = assert_error(c.post(f"{BASE}/send"), 429, "gmail_rate_limited")
    assert err["details"][0]["message"] == "retry_after_seconds=120"
    row = consent(engine, c.user_id)
    assert row["status"] == "enabled" and row["consecutive_failures"] == 1 and row["last_status"] == "rate_limited"
    assert "email.report_failed" in audit_types(engine, c.user_id)


def test_delivery_failures_are_retried_a_bounded_number_of_times_then_reported(user_client, fake_google, engine, apis):
    c = user_client(report_max_retries=2)
    enable_ok(c, fake_google, engine)
    apis.send_behavior = [error(503)]
    assert_error(c.post(f"{BASE}/send"), 502, "email_delivery_failed")
    assert len(apis.send_calls) == 3                                               # 1 try + 2 retries, then it stops
    assert consent(engine, c.user_id)["consecutive_failures"] == 1 and consent(engine, c.user_id)["status"] == "enabled"
    apis.send_behavior = [error(503), lambda: __import__("httpx").Response(200, json={"id": "m"})]
    apis.send_calls.clear()
    assert c.post(f"{BASE}/send").status_code == 200 and consent(engine, c.user_id)["consecutive_failures"] == 0


def test_a_changed_encryption_key_asks_for_consent_again_instead_of_crashing(user_client, make_client, make_reports,
                                                                           fake_google, engine, apis):
    c = user_client()
    enable_ok(c, fake_google, engine)
    rotated = make_client(as_user=c.user_id, report_service=make_reports())        # a new random key
    assert_error(rotated.post(f"{BASE}/send"), 409, "consent_invalid")
    row = consent(engine, c.user_id)
    assert row["status"] == "error" and row["encrypted_refresh_token"] is None and apis.token_calls == []


# ---------------------------------------------------------------- withdrawal

def test_withdrawal_revokes_at_google_and_deletes_the_token(user_client, fake_google, engine, apis):
    c = user_client()
    enable_ok(c, fake_google, engine)
    out = c.post(f"{BASE}/disable").json()
    assert out == {"enabled": False, "revoked_at_google": True, "note": None}
    assert apis.revoke_calls == [{"token": "1//refresh-NOT-REAL"}]
    row = consent(engine, c.user_id)
    assert row["status"] == "disabled" and row["encrypted_refresh_token"] is None and row["disabled_at"] is not None
    assert "email.consent_withdrawn" in audit_types(engine, c.user_id)
    assert_error(c.post(f"{BASE}/send"), 409, "email_reports_not_enabled")
    assert c.post(f"{BASE}/disable").json()["enabled"] is False                    # idempotent


def test_withdrawal_still_deletes_locally_when_google_is_unreachable(user_client, fake_google, engine, apis):
    c = user_client()
    enable_ok(c, fake_google, engine)
    apis.revoke_behavior = [error(500)]
    out = c.post(f"{BASE}/disable").json()
    assert out["enabled"] is False and out["revoked_at_google"] is False and "myaccount.google.com/permissions" in out["note"]
    assert consent(engine, c.user_id)["encrypted_refresh_token"] is None


def test_consent_is_per_user(user_client, make_client, fake_google, engine):
    a = user_client()
    enable_ok(a, fake_google, engine)
    b = make_client(report_service=a.svc)
    assert b.get(f"{BASE}/status").json()["enabled"] is False
    assert_error(b.post(f"{BASE}/send"), 409, "email_reports_not_enabled")


def test_deleting_a_user_deletes_their_consent(user_client, fake_google, engine):
    c = user_client()
    enable_ok(c, fake_google, engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM auth.users WHERE user_id = :u"), {"u": c.user_id})
    assert consent(engine, c.user_id) is None


# ---------------------------------------------------------------- audit independence

def test_audit_log_is_the_record_and_email_only_adds_events(user_client, fake_google, engine, apis):
    c = user_client()
    audit_for(engine, c.user_id)
    with engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM audit.events WHERE user_id = :u"), {"u": c.user_id}).scalar_one()
    apis.send_behavior = [error(503)]
    enable_ok(c, fake_google, engine)
    c.post(f"{BASE}/send")                                              # fails
    with engine.connect() as conn:
        after = conn.execute(text("SELECT count(*) FROM audit.events WHERE user_id = :u"), {"u": c.user_id}).scalar_one()
    assert after > before                                               # events are kept whatever happens to email
    page = c.get("/api/audit/events", params={"event_type": "email"}).json()
    assert {"email.report_failed", "email.report_attempted"} <= {e["event_type"] for e in page["items"]}
    assert "ya29" not in json.dumps(page) and "refresh" not in json.dumps(page).replace("refresh_token_stored", "")


def test_build_summary_counts_only_recent_own_events(engine, make_user):
    import datetime as dt
    me, other = make_user(), make_user()
    audit_for(engine, me, n=3)
    audit_for(engine, other, n=5)
    s = build_summary(sessionmaker(bind=engine)(), me, since=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7), period_days=7)
    assert s["questions_asked"] == 3 and s["distinct_cases_worked_with"] == 1
    empty = build_summary(sessionmaker(bind=engine)(), me, since=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1), period_days=7)
    assert empty["questions_asked"] == 0 and empty["median_answer_seconds"] is None
