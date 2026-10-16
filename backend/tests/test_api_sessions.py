"""Chat-session endpoints: ownership by the signed-in Google user, CSRF on POST."""
import uuid

import pytest
from sqlalchemy import text

from tests.test_api_patients import assert_error

pytestmark = pytest.mark.usefixtures("clean_chat")


def create(client, **body):
    return client.post("/api/sessions", json={"patient_id": "P-004", **body})


def test_create_session_owned_by_signed_in_user(client, engine):
    r = create(client, title="  Maintenance vs detox  ", mode="both")
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["patient_id"] == "P-004" and s["title"] == "Maintenance vs detox" and s["mode"] == "both"
    assert s["message_count"] == 0
    with engine.connect() as c:
        owner = c.execute(text("SELECT owner_user_id::text FROM chat.sessions WHERE session_id = :s"),
                          {"s": s["session_id"]}).scalar_one()
    assert owner == client.user_id


def test_create_defaults(client):
    s = create(client).json()
    assert s["mode"] == "rag" and s["title"] is None


def test_create_unknown_patient_is_422(client):
    err = assert_error(create(client, patient_id="P-099"), 422, "unknown_patient")
    assert err["details"][0]["loc"] == ["body", "patient_id"]


@pytest.mark.parametrize("body", [
    {"patient_id": "P-4"},
    {"patient_id": "P-004", "mode": "turbo"},
    {"patient_id": "P-004", "title": "   "},
    {"patient_id": "P-004", "title": "x" * 121},
    {"patient_id": "P-004", "owner_user_id": str(uuid.uuid4())},  # owner can never be chosen by the client
    {},
])
def test_create_rejects_invalid_body(client, body):
    assert_error(client.post("/api/sessions", json=body), 422, "validation_error")


def test_create_rejects_non_json(client):
    r = client.post("/api/sessions", content="patient_id=P-004", headers={"Content-Type": "text/plain"})
    assert_error(r, 422, "validation_error")


# ---------- CSRF ----------

def test_create_requires_csrf_header(client):
    r = client.post("/api/sessions", json={"patient_id": "P-004"}, headers={"X-CSRF-Token": ""})
    assert_error(r, 403, "csrf_failed")


def test_create_rejects_wrong_csrf_token(client):
    assert_error(client.post("/api/sessions", json={"patient_id": "P-004"},
                             headers={"X-CSRF-Token": "not-the-token" * 4}), 403, "csrf_failed")


def test_csrf_token_of_another_session_is_rejected(make_client):
    a, b = make_client(), make_client()
    r = a.post("/api/sessions", json={"patient_id": "P-004"}, headers={"X-CSRF-Token": b.headers["X-CSRF-Token"]})
    assert_error(r, 403, "csrf_failed")


def test_create_rejects_foreign_origin(client):
    assert_error(client.post("/api/sessions", json={"patient_id": "P-004"},
                             headers={"Origin": "https://evil.example"}), 403, "csrf_failed")


def test_create_rejects_cross_site_fetch_without_origin(client):
    client.headers.pop("Origin", None)
    r = client.post("/api/sessions", json={"patient_id": "P-004"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert_error(r, 403, "csrf_failed")


# ---------- listing and ownership ----------

def test_list_only_my_sessions_newest_first(make_client):
    me, other = make_client(), make_client()
    first = create(me, title="first").json()
    second = create(me, title="second", patient_id="P-001").json()
    create(other, title="not mine")

    body = me.get("/api/sessions").json()
    assert body["total"] == 2
    assert [s["session_id"] for s in body["items"]] == [second["session_id"], first["session_id"]]
    assert other.get("/api/sessions").json()["total"] == 1


def test_same_user_new_login_sees_own_sessions(make_client, make_user):
    user = make_user()
    laptop, phone = make_client(as_user=user), make_client(as_user=user)
    sid = create(laptop).json()["session_id"]
    assert [s["session_id"] for s in phone.get("/api/sessions").json()["items"]] == [sid]


def test_list_filter_and_pagination(client):
    for i in range(3):
        create(client, title=f"s{i}")
    create(client, patient_id="P-002")
    body = client.get("/api/sessions", params={"patient_id": "P-004", "limit": 2}).json()
    assert body["total"] == 3 and len(body["items"]) == 2
    assert all(s["patient_id"] == "P-004" for s in body["items"])
    assert_error(client.get("/api/sessions", params={"patient_id": "bad"}), 422, "validation_error")


def test_messages_in_order_with_count(client, engine):
    sid = create(client).json()["session_id"]
    with engine.begin() as c:
        for role, content in [("user", "What does the corpus say?"), ("assistant", "Mock answer.")]:
            c.execute(text("INSERT INTO chat.messages (session_id, role, content, mode, correlation_id) "
                           "VALUES (:s, :r, :c, 'rag', 'cid-test')"), {"s": sid, "r": role, "c": content})
    body = client.get(f"/api/sessions/{sid}/messages").json()
    assert body["session_id"] == sid and body["total"] == 2
    assert [m["role"] for m in body["items"]] == ["user", "assistant"]
    assert client.get("/api/sessions").json()["items"][0]["message_count"] == 2


def test_messages_of_new_session_are_empty(client):
    sid = create(client).json()["session_id"]
    body = client.get(f"/api/sessions/{sid}/messages").json()
    assert body["items"] == [] and body["total"] == 0


def test_messages_ownership_check(make_client, engine):
    me, other = make_client(), make_client()
    sid = create(me).json()["session_id"]
    with engine.begin() as c:
        c.execute(text("INSERT INTO chat.messages (session_id, role, content, mode) "
                       "VALUES (:s, 'user', 'private question', 'rag')"), {"s": sid})
    # Someone else's session looks exactly like a missing one, and leaks no content.
    r = other.get(f"/api/sessions/{sid}/messages")
    err_other = assert_error(r, 404, "session_not_found")
    assert "private question" not in r.text
    err_missing = assert_error(me.get(f"/api/sessions/{uuid.uuid4()}/messages"), 404, "session_not_found")
    assert err_other["message"] == err_missing["message"]


def test_messages_malformed_session_id_is_422(client):
    assert_error(client.get("/api/sessions/not-a-uuid/messages"), 422, "validation_error")


def test_deleting_a_user_removes_their_sessions(make_client, engine):
    c = make_client()
    sid = create(c).json()["session_id"]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM auth.users WHERE user_id = :u"), {"u": c.user_id})
        assert conn.execute(text("SELECT count(*) FROM chat.sessions WHERE session_id = :s"),
                            {"s": sid}).scalar_one() == 0
