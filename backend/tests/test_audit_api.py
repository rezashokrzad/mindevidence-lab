"""Audit events API, sign-in/sign-out auditing, UI events, and the activity dashboard data."""
import json

import pytest
from sqlalchemy import text

from app.audit import AuditLog
from app.retrieval.embedder import HashEmbedder
from tests.fake_llm import ScriptedProvider, answer_json, claim, reply
from tests.test_api_answers import OPIOID_Q, ask, make_api, rag_provider  # noqa: F401
from tests.test_api_patients import assert_error
from tests.test_api_retrieval import indexed  # noqa: F401
from tests.test_auth import callback, fresh_sub, sign_in, start_login

pytestmark = pytest.mark.usefixtures("needs_vector")


@pytest.fixture
def audit(engine):
    from sqlalchemy.orm import sessionmaker
    return AuditLog(sessionmaker(bind=engine))


def events(engine, **where):
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "true"
    with engine.connect() as c:
        return c.execute(text(f"SELECT event_type, severity, status, metadata, user_id::text AS uid FROM audit.events "
                              f"WHERE {clause} ORDER BY event_seq"), where).mappings().all()


# ---------------------------------------------------------------- sign-in / sign-out auditing

def test_sign_in_and_sign_out_are_audited(make_client, fake_google, engine):
    sub = fresh_sub()
    c = make_client(as_user=None)
    assert "signin=success" in sign_in(c, fake_google, sub=sub, email="audit@example.edu").headers["location"]
    with engine.connect() as conn:
        uid = conn.execute(text("SELECT user_id::text FROM auth.users WHERE google_sub = :s"), {"s": sub}).scalar_one()
    rows = events(engine, user_id=uid)
    assert [r["event_type"] for r in rows] == ["auth.sign_in"] and rows[0]["metadata"]["method"] == "google"
    assert "audit@example.edu" not in json.dumps(rows[0]["metadata"])           # no email in the event either

    csrf = c.get("/api/auth/session").json()["csrf_token"]
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:5173"}).status_code == 204
    assert [r["event_type"] for r in events(engine, user_id=uid)] == ["auth.sign_in", "auth.sign_out"]


def test_failed_sign_ins_are_audited_without_secrets(make_client, fake_google, engine):
    with engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM audit.events WHERE event_type = 'auth.sign_in_failed'")).scalar_one()
    c = make_client(as_user=None)
    _, auth = start_login(c)
    r = callback(c, code="the-oauth-code-123", state="wrong-state")
    assert "auth_error=invalid_state" in r.headers["location"]
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT metadata::text, severity FROM audit.events WHERE event_type = 'auth.sign_in_failed' "
                                 "ORDER BY event_seq DESC LIMIT 1")).one()
        after = conn.execute(text("SELECT count(*) FROM audit.events WHERE event_type = 'auth.sign_in_failed'")).scalar_one()
    assert after == before + 1 and rows.severity == "warning" and "invalid_state" in rows[0]
    assert "the-oauth-code-123" not in rows[0] and "wrong-state" not in rows[0]


# ---------------------------------------------------------------- browsing events

def test_students_see_only_their_own_events(make_client, audit, make_user):
    a_user, b_user = make_user(), make_user()
    a, b = make_client(as_user=a_user), make_client(as_user=b_user)
    audit.record("test.event_a", user_id=a_user, correlation_id="cid-a1", metadata={"n": 1})
    audit.record("test.event_b", user_id=b_user, correlation_id="cid-b1", metadata={"n": 2})
    mine = a.get("/api/audit/events", params={"event_type": "test"}).json()
    assert [e["event_type"] for e in mine["items"]] == ["test.event_a"] and mine["scope"] == "own"
    greedy = a.get("/api/audit/events", params={"scope": "all", "event_type": "test"}).json()
    assert greedy["scope"] == "own" and [e["event_type"] for e in greedy["items"]] == ["test.event_a"]
    assert a.get("/api/audit/events", params={"correlation_id": "cid-b1"}).json()["items"] == []
    assert b.get("/api/audit/events", params={"correlation_id": "cid-b1"}).json()["total"] == 1


def test_instructors_can_review_all_events(make_client, audit, make_user):
    student_id, teacher_id = make_user(), make_user(role="instructor")
    audit.record("test.student_event", user_id=student_id, correlation_id="cid-s1")
    teacher = make_client(as_user=teacher_id)
    page = teacher.get("/api/audit/events", params={"scope": "all", "event_type": "test.student"}).json()
    assert page["scope"] == "all" and [e["event_type"] for e in page["items"]] == ["test.student_event"]
    assert teacher.get("/api/audit/events", params={"event_type": "test.student"}).json()["total"] == 0   # default stays own


def test_event_filters_pagination_and_validation(make_client, audit, make_user):
    uid = make_user()
    c = make_client(as_user=uid)
    for i in range(5):
        audit.record("test.filter", user_id=uid, correlation_id="cid-f", severity="error" if i == 4 else "info", metadata={"i": i})
    page = c.get("/api/audit/events", params={"correlation_id": "cid-f", "limit": 2}).json()
    assert page["total"] == 5 and len(page["items"]) == 2 and page["items"][0]["metadata"]["i"] == 4      # newest first
    assert c.get("/api/audit/events", params={"correlation_id": "cid-f", "min_severity": "error"}).json()["total"] == 1
    for bad in ({"limit": 0}, {"min_severity": "fatal"}, {"event_type": "BAD TYPE!"}, {"correlation_id": "bad id"}):
        assert_error(c.get("/api/audit/events", params=bad), 422, "validation_error")


def test_audit_requires_sign_in(make_client):
    anon = make_client(as_user=None)
    for path in ("/api/audit/events", "/api/audit/activity"):
        assert_error(anon.get(path), 401, "authentication_required")


# ---------------------------------------------------------------- UI events

def test_ui_events_are_recorded_and_validated(make_client, engine):
    c = make_client()
    assert c.post("/api/audit/ui-events", json={"event_type": "patient.selected", "patient_id": "P-004"}).json() == {"recorded": True}
    assert c.post("/api/audit/ui-events", json={"event_type": "mode.changed", "mode": "both"}).status_code == 200
    ok = c.post("/api/audit/ui-events", json={"event_type": "citation.opened", "correlation_id": "cid-x",
                                              "chunk_id": "NIDA-PDAT-c0007"})
    assert ok.status_code == 200
    kinds = [r["event_type"] for r in events(engine, user_id=c.user_id)]
    assert kinds == ["patient.selected", "mode.changed", "citation.opened"]
    assert events(engine, user_id=c.user_id)[0]["metadata"]["source"] == "ui"

    assert_error(c.post("/api/audit/ui-events", json={"event_type": "patient.selected", "patient_id": "P-099"}), 422, "unknown_patient")
    for bad in ({"event_type": "patient.selected"}, {"event_type": "mode.changed"}, {"event_type": "free.form", "x": 1},
                {"event_type": "mode.changed", "mode": "turbo"}, {"event_type": "citation.opened", "chunk_id": "x"},
                {"event_type": "patient.selected", "patient_id": "P-004", "extra": "field"}):
        assert_error(c.post("/api/audit/ui-events", json=bad), 422, "validation_error")
    assert_error(c.post("/api/audit/ui-events", json={"event_type": "mode.changed", "mode": "gk"},
                        headers={"X-CSRF-Token": "bad" * 20}), 403, "csrf_failed")


# ---------------------------------------------------------------- activity dashboard data

def test_activity_traces_a_question_from_submission_to_validation(make_api):
    provider = ScriptedProvider(reply(answer_json([claim("Maintenance treatment is described.", "evidence", ["E1"]),
                                                   claim("Unbacked statement.", "evidence", [])])))
    c = make_api(provider)
    cid = ask(c).json()["correlation_id"]
    a = c.get("/api/audit/activity").json()

    assert a["totals"]["answers"] == 1 and a["totals"]["answered"] == 1 and a["totals"]["retrievals"] == 1
    assert a["totals"]["unsupported_claims"] == 1 and a["totals"]["errors"] == 0
    assert a["totals"]["median_latency_ms"] is not None and a["totals"]["max_latency_ms"] >= a["totals"]["median_latency_ms"]
    ans = a["answers"][0]
    assert ans["correlation_id"] == cid and ans["mode"] == "rag" and ans["outcome"] == "answered"
    assert ans["patient_id"] == "P-004" and ans["claims"] == 2 and ans["unsupported_claims"] == 1
    assert [n["node"] for n in ans["nodes"]] == ["validate_input", "classify", "decide_retrieval", "retrieve",
                                                 "assess_evidence", "generate", "validate_citations", "finalize"]
    assert all(n["reason"] for n in ans["nodes"])
    r = ans["retrieval"]
    assert r["result_count"] > 0 and r["chunk_ids"] and r["settings"]["metric"] == "cosine" and r["top_similarity"] > 0
    assert ans["model"]["request"]["mode"] == "rag" and ans["model"]["status"] == "ok"
    assert ans["model"]["response"]["calls"][0]["model"] == "scripted-model"
    assert a["model_config_in_use"]["provider"] == "scripted"
    types = [e["event_type"] for e in a["events"]]
    assert {"question.submitted", "retrieval.executed", "llm.request", "llm.response", "citation.validated",
            "answer.completed"} <= set(types)
    assert a["events"][0]["event_seq"] > a["events"][-1]["event_seq"]          # newest first


def test_activity_lists_errors_and_failed_answers(make_api):
    from app.llm.provider import LLMTimeout
    c = make_api(ScriptedProvider(LLMTimeout("The language model did not respond in time.")))
    assert ask(c).status_code == 504
    a = c.get("/api/audit/activity").json()
    assert a["totals"]["errors"] >= 2 and a["answers"][0]["outcome"] == "error" and a["answers"][0]["outcome_code"] == "llm_timeout"
    assert any("llm_timeout" in e for e in a["answers"][0]["errors"])
    assert {e["event_type"] for e in a["errors"]} >= {"llm.response", "answer.completed"}
    assert all(e["severity"] == "error" for e in a["errors"])


def test_activity_covers_only_my_events_and_only_this_login(make_api, make_user):
    me_id = make_user()
    me, other = make_api(rag_provider(), as_user=me_id), make_api(rag_provider())
    ask(me)
    ask(other)
    assert me.get("/api/audit/activity").json()["totals"]["answers"] == 1
    later_login = make_api(rag_provider(), as_user=me_id)          # same user, a new device/login
    assert later_login.get("/api/audit/activity").json()["totals"]["answers"] == 0
    ask(later_login)
    assert later_login.get("/api/audit/activity").json()["totals"]["answers"] == 1


def test_activity_shows_feedback_and_refusals(make_api):
    c = make_api(rag_provider())
    cid = ask(c).json()["correlation_id"]
    c.post("/api/feedback", json={"correlation_id": cid, "rating": "helpful"})
    refused = ask(make_api(ScriptedProvider(AssertionError()), as_user=None) if False else c, "What dose should he get?")
    assert refused.json()["outcome"]["kind"] == "refused"
    a = c.get("/api/audit/activity").json()
    by_cid = {x["correlation_id"]: x for x in a["answers"]}
    assert by_cid[cid]["feedback"] == "helpful"
    assert a["totals"]["refused_or_unsupported"] == 1 and a["totals"]["answered"] == 1
