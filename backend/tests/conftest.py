"""Shared fixtures.

Database tests need TEST_DATABASE_URL (see .env.example). They rebuild the
schema from scratch (downgrade to base, upgrade to head, seed), so the database
name must end in "_test". Without it, database tests are skipped and the
seed-file tests still run.
"""
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import BACKEND_DIR  # also loads backend/.env
from db.seed import load_seed, seed

QUERIES_DIR = BACKEND_DIR / "db" / "queries"
CHECKS_FILE = BACKEND_DIR / "db" / "checks" / "consistency_checks.sql"


def _test_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        return None
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if not (make_url(url).database or "").endswith("_test"):
        pytest.exit("TEST_DATABASE_URL must point to a database whose name ends in '_test'", returncode=2)
    return url


def alembic_config(url: str):
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def seed_data() -> dict:
    return load_seed()


@pytest.fixture(scope="session")
def db_url() -> str:
    url = _test_url()
    if url is None:
        pytest.skip("TEST_DATABASE_URL not set; skipping database tests")
    return url


@pytest.fixture(scope="session")
def engine(db_url):
    """A clean database: downgrade to base, upgrade to head, seed."""
    from alembic import command

    probe = create_engine(db_url, connect_args={"connect_timeout": 5})
    try:
        with probe.connect():
            pass
    except Exception as exc:  # wrong password, server down, database missing
        first_line = str(exc).strip().splitlines()[0][:200]
        pytest.exit(f"TEST_DATABASE_URL is set but the test database is unreachable ({first_line}). "
                    "Fix the credentials in backend/.env, or unset TEST_DATABASE_URL to run only the "
                    "database-free tests.", returncode=3)
    finally:
        probe.dispose()

    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    # Migration 0005 needs the pgvector extension. Without it, build up to 0004 so everything
    # else is still tested; tests that need vectors are skipped (see the vector_available fixture).
    with create_engine(db_url).connect() as c:
        _DB_STATE["vector"] = bool(c.execute(text(
            "SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'")).scalar())
    _DB_STATE["head"] = "0007_auto_mode" if _DB_STATE["vector"] else "0004_document_ingestion"
    command.upgrade(cfg, _DB_STATE["head"])
    seed("TEST_DATABASE_URL")
    eng = create_engine(db_url)
    yield eng
    eng.dispose()


_DB_STATE: dict = {}


@pytest.fixture(scope="session")
def vector_available(engine) -> bool:
    return _DB_STATE["vector"]


@pytest.fixture(scope="session")
def head_revision(engine) -> str:
    return _DB_STATE["head"]


@pytest.fixture
def needs_vector(vector_available):
    if not vector_available:
        pytest.skip("pgvector is not installed on the test PostgreSQL server")


@pytest.fixture
def conn(engine):
    """A connection whose changes are always rolled back."""
    with engine.connect() as c:
        tx = c.begin()
        yield c
        tx.rollback()


def _no_email_settings():
    from app.reports.config import ReportSettings

    return ReportSettings(email_token_encryption_key="")


TEST_SESSION_SECRET = "t3st-only-" + "k" * 40  # never used outside tests
FRONTEND = "http://localhost:5173"


def base_settings(db_url: str, **overrides):
    from app.config import Settings

    from tests.fake_google import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI

    values = {"database_url": db_url, "app_env": "test",
              "cors_origins": "http://localhost:5173,http://127.0.0.1:5173",
              "frontend_url": FRONTEND + "/",
              "google_client_id": CLIENT_ID, "google_client_secret": CLIENT_SECRET,
              "google_redirect_uri": REDIRECT_URI, "session_secret": TEST_SESSION_SECRET,
              "cookie_secure": False}
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def make_settings(db_url):
    """Build API settings for the test database; keyword overrides win over the environment."""
    return lambda **overrides: base_settings(db_url, **overrides)


@pytest.fixture
def fake_google():
    from tests.fake_google import FakeGoogle

    return FakeGoogle()


@pytest.fixture
def make_user(engine):
    """Insert a user directly (as a completed Google sign-in would)."""
    import uuid as _uuid

    def _make(*, email=None, role="student", is_active=True, display_name="Test User"):
        sub = str(_uuid.uuid4().int)[:21]
        with engine.begin() as c:
            return c.execute(text(
                "INSERT INTO auth.users (google_sub, email, email_verified, display_name, role, is_active) "
                "VALUES (:sub, :email, true, :name, :role, :active) RETURNING user_id"),
                {"sub": sub, "email": email or f"user{sub[-6:]}@example.edu", "name": display_name,
                 "role": role, "active": is_active}).scalar_one()

    return _make


@pytest.fixture
def make_client(engine, make_settings, fake_google, make_user):
    """Factory for TestClients bound to the seeded test database.

    as_user: None → anonymous; "new" → a fresh student; a UUID → that user.
    Signed-in clients carry the session cookie and send X-CSRF-Token + Origin by default,
    like the real frontend does.
    """
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.auth import create_login_session

    clients = []

    def _make(as_user="new", google=None, ingestion_settings=None, embedder=None, retrieval_settings=None,
              llm_provider=None, llm_settings=None, report_service=None, report_settings=None, **overrides):
        from app.llm.config import LLMSettings
        from app.llm.provider import LLMNotConfigured
        from app.retrieval.config import RetrievalSettings
        from app.retrieval.embedder import HashEmbedder
        from tests.fake_llm import ScriptedProvider

        settings = make_settings(**overrides)
        app = create_app(settings, google=google or fake_google.client(), ingestion_settings=ingestion_settings,
                         embedder=embedder or HashEmbedder(),
                         retrieval_settings=retrieval_settings or RetrievalSettings(embedding_backend="hash"),
                         llm_provider=llm_provider or ScriptedProvider(LLMNotConfigured("no model is available in this test")),
                         llm_settings=llm_settings or LLMSettings(deepseek_api_key="test-key-not-real"),
                         report_service=report_service,
                         report_settings=report_settings or _no_email_settings())
        client = TestClient(app, raise_server_exceptions=False)
        clients.append(client)
        if as_user is not None:
            user_id = make_user() if as_user == "new" else as_user
            with app.state.session_factory() as db:
                token = create_login_session(db, settings, user_id)
                db.commit()
            client.cookies.set(settings.session_cookie_name, token)
            csrf = client.get("/api/auth/session").json()["csrf_token"]
            client.headers.update({"X-CSRF-Token": csrf, "Origin": FRONTEND})
            client.user_id = str(user_id)
        return client

    yield _make
    for c in clients:
        c.close()


@pytest.fixture
def client(make_client):
    """A signed-in student."""
    return make_client()


@pytest.fixture
def anon_client(make_client):
    return make_client(as_user=None)


@pytest.fixture
def clean_chat(engine):
    """Empty the chat tables before a test (patient cases are untouched)."""
    with engine.begin() as c:
        c.execute(text("TRUNCATE chat.sessions CASCADE"))
    yield


def read_query(name: str) -> str:
    """Load a db/queries file, converting psql's :'patient_id' to a bind parameter."""
    sql = (QUERIES_DIR / name).read_text(encoding="utf-8")
    return sql.replace(":'patient_id'", ":patient_id")


def load_checks() -> list[tuple[str, str]]:
    """Split consistency_checks.sql into (name, sql) pairs."""
    blocks = re.split(r"^-- name: (\w+)\s*$", CHECKS_FILE.read_text(encoding="utf-8"), flags=re.M)
    return [(blocks[i], blocks[i + 1].strip()) for i in range(1, len(blocks), 2)]


def scalar(conn, sql: str, **params):
    return conn.execute(text(sql), params).scalar_one()
