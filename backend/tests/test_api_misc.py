"""Health, papers, CORS, request ids, error envelope, and the OpenAPI document."""
import pytest
from pydantic import ValidationError

from tests.test_api_patients import assert_error

FRONTEND = "http://localhost:5173"


# ---------- health ----------

def test_health_ok(client, head_revision):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert body["schema_version"] == head_revision
    assert body["sign_in"] == "google"


def test_health_reports_database_down(make_client):
    down = make_client(as_user=None, database_url="postgresql+psycopg://nobody@127.0.0.1:1/nothing_test")
    r = down.get("/api/health")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded" and r.json()["database"] == "unavailable"


def test_database_down_gives_503_envelope(make_client):
    down = make_client(as_user=None, database_url="postgresql+psycopg://nobody@127.0.0.1:1/nothing_test")
    down.cookies.set("me_session", "x" * 43)  # forces a session lookup against the dead database
    assert_error(down.get("/api/patients"), 503, "database_unavailable")


# ---------- cross-cutting ----------

def test_request_id_echoed_or_generated(client):
    assert client.get("/api/health", headers={"X-Request-ID": "trace-123"}).headers["X-Request-ID"] == "trace-123"
    generated = client.get("/api/health", headers={"X-Request-ID": "bad id with spaces"}).headers["X-Request-ID"]
    assert generated != "bad id with spaces" and len(generated) == 32


def test_unknown_route_uses_envelope(client):
    assert_error(client.get("/api/nope"), 404, "not_found")


def test_method_not_allowed_uses_envelope(client):
    assert_error(client.delete("/api/patients"), 405, "method_not_allowed")


def test_cors_allows_local_frontend(client):
    r = client.options("/api/patients", headers={
        "Origin": FRONTEND, "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == FRONTEND
    assert "DELETE" not in r.headers["access-control-allow-methods"]
    assert r.headers["access-control-allow-credentials"] == "true"
    pre = client.options("/api/sessions", headers={
        "Origin": FRONTEND, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-csrf-token"})
    assert pre.status_code == 200
    assert client.get("/api/patients", headers={"Origin": FRONTEND}).headers[
        "access-control-allow-origin"] == FRONTEND


def test_cors_rejects_other_origins(client):
    r = client.options("/api/patients", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/api/patients", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_wildcard_is_refused(make_settings):
    with pytest.raises(ValidationError):
        make_settings(cors_origins="*")


# ---------- OpenAPI ----------

def test_openapi_documents_every_endpoint(client):
    spec = client.get("/api/openapi.json").json()
    ops = {(path, method) for path, item in spec["paths"].items() for method in item}
    assert ops == {
        ("/api/health", "get"), ("/api/patients", "get"), ("/api/patients/{patient_id}", "get"),
        ("/api/patients/{patient_id}/history", "get"),
        ("/api/documents", "get"), ("/api/documents/{document_id}", "get"),
        ("/api/documents/{document_id}/chunks", "get"), ("/api/chunks/{chunk_id}", "get"),
        ("/api/admin/ingestion/runs", "get"), ("/api/admin/ingestion/runs", "post"),
        ("/api/admin/ingestion/runs/{run_id}", "get"),
        ("/api/retrieval/search", "post"), ("/api/retrieval/status", "get"),
        ("/api/answers", "post"), ("/api/answers/{correlation_id}/trace", "get"), ("/api/feedback", "post"),
        ("/api/audit/events", "get"), ("/api/audit/activity", "get"), ("/api/audit/ui-events", "post"),
        ("/api/reports/email/status", "get"), ("/api/reports/email/enable", "post"),
        ("/api/reports/email/callback", "get"), ("/api/reports/email/disable", "post"),
        ("/api/reports/email/send", "post"),
        ("/api/sessions", "get"), ("/api/sessions", "post"), ("/api/sessions/{session_id}/messages", "get"),
        ("/api/auth/login", "get"), ("/api/auth/callback", "get"), ("/api/auth/session", "get"),
        ("/api/auth/logout", "post"),
    }


def test_openapi_schemas_present(client):
    schemas = client.get("/api/openapi.json").json()["components"]["schemas"]
    for name in ["PatientSummary", "PatientPage", "PatientDetail", "HistoryPage", "HistoryEventOut",
                 "DocumentPage", "DocumentDetail", "ChunkDetail", "ChunkPage", "IngestionRunOut", "SessionCreate", "SessionOut", "SessionPage", "MessagePage",
                 "HealthOut", "ErrorResponse", "PartialDate", "AuthSessionOut", "UserOut"]:
        assert name in schemas, name
    # FastAPI's default validation schema is replaced by the shared error envelope.
    assert "HTTPValidationError" not in schemas
    assert schemas["SessionCreate"]["additionalProperties"] is False


def test_openapi_errors_use_envelope_and_policy_is_documented(client):
    spec = client.get("/api/openapi.json").json()
    detail = spec["paths"]["/api/patients/{patient_id}"]["get"]["responses"]
    for status in ("404", "422"):
        assert detail[status]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    post = spec["paths"]["/api/sessions"]["post"]
    assert "x-csrf-token" in post["description"].lower()
    assert {"401", "403"} <= set(post["responses"])
    for path in ("/api/patients", "/api/patients/{patient_id}", "/api/documents", "/api/chunks/{chunk_id}",
                 "/api/sessions"):
        assert "401" in spec["paths"][path]["get"]["responses"], path


def test_docs_page_served(client):
    r = client.get("/api/docs")
    assert r.status_code == 200 and "swagger" in r.text.lower()


def test_unexpected_errors_use_envelope_and_keep_cors_headers(client):
    """A crash must not turn into an opaque browser CORS error: the client has to read the envelope."""
    client.app.add_api_route("/api/_boom", lambda: 1 / 0, methods=["GET"])
    r = client.get("/api/_boom", headers={"Origin": FRONTEND})
    assert_error(r, 500, "internal_error")
    assert r.headers["access-control-allow-origin"] == FRONTEND
    assert "division" not in r.text.lower()                                  # nothing internal leaks
