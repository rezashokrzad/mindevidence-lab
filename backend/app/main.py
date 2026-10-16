"""FastAPI application factory.

Run (from backend/):  uvicorn app.main:create_app --factory --reload --port 8000
OpenAPI:              http://localhost:8000/api/docs  (JSON at /api/openapi.json)
"""
import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import error_json, install_error_handlers
from app.api.routes import admin, answers, audit, auth, documents, health, patients, reports, retrieval, sessions
from app.audit import AuditLog
from app.config import API_VERSION, Settings, get_settings
from app.database import build_engine, build_session_factory
from app.ingestion.config import IngestionSettings
from app.integrations.google import GoogleOIDCClient
from app.llm.config import LLMSettings
from app.reports.config import ReportSettings
from app.reports.gmail import GmailClient
from app.reports.service import ReportService
from app.reports.vault import TokenVault
from app.llm.deepseek import DeepSeekProvider
from app.llm.provider import LLMProvider
from app.retrieval.config import RetrievalSettings
from app.retrieval.embedder import Embedder, build_embedder
from app.security import CSRF_HEADER
from app.workflow.graph import build_graph
from app.workflow.nodes import WorkflowDeps

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

DESCRIPTION = """
Backend for **MindEvidence Lab**, an educational research assistant for psychology students who study
addiction treatment.

* All patient records are **synthetic** teaching cases (P-001…P-010). This is not a clinical tool.
* Errors share one envelope: `{"error": {"code", "message", "details", "request_id"}}`.
* Lists use offset pagination: `limit` (1–100, default 20) and `offset`.
* Every response carries `X-Request-ID`. Send your own (letters, digits, `._-`, up to 64 characters) to correlate logs.

**Authentication:** Google sign-in (OpenID Connect, scopes `openid email profile` only) with a
server-side session in an HttpOnly cookie. Every endpoint except `/api/health` and `/api/auth/*`
requires sign-in (401 `authentication_required`). POST requests also need the `X-CSRF-Token` header
from `GET /api/auth/session` (403 `csrf_failed`). Swagger's "Try it out" works only after signing in
in the same browser (`/api/auth/login`). Document ingestion is instructor-only (`/api/admin/*`).
Semantic retrieval (`/api/retrieval/search`) returns research chunks without any LLM; `/api/answers` runs the DeepSeek answer workflow with structured, validated citations.
"""

TAGS = [
    {"name": "health", "description": "Service status"},
    {"name": "auth", "description": "Google sign-in, session inspection, sign-out"},
    {"name": "patients", "description": "Synthetic patient cases (read-only, sign-in required)"},
    {"name": "documents", "description": "Research documents and their traceable text chunks (sign-in required)"},
    {"name": "retrieval", "description": "Semantic search over research chunks (pgvector, cosine, no LLM)"},
    {"name": "answers", "description": "Question answering through the LangGraph workflow (DeepSeek), traces, feedback"},
    {"name": "audit", "description": "Audit events and the activity dashboard (own events only)"},
    {"name": "reports", "description": "Optional opt-in Gmail activity summaries (counts only)"},
    {"name": "sessions", "description": "Chat sessions owned by the signed-in user"},
    {"name": "admin", "description": "Document ingestion (instructor role only)"},
]


def create_app(settings: Settings | None = None, google: GoogleOIDCClient | None = None,
               ingestion_settings: IngestionSettings | None = None, embedder: Embedder | None = None,
               retrieval_settings: RetrievalSettings | None = None, llm_provider: LLMProvider | None = None,
               llm_settings: LLMSettings | None = None, report_settings: ReportSettings | None = None,
               report_service: ReportService | None = None) -> FastAPI:
    settings = settings or get_settings()
    google = google or GoogleOIDCClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        redirect_uri=settings.google_redirect_uri,
    )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    engine = build_engine(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if _app.state.retrieval_settings.embedding_preload:
            # Load the model off the request path so the first question is not slow. Failures surface on first use.
            import threading

            def _warm() -> None:
                try:
                    _ = _app.state.embedder.version
                except Exception:
                    pass
            threading.Thread(target=_warm, daemon=True, name="embedder-warmup").start()
        yield
        google.close()
        engine.dispose()

    app = FastAPI(
        title="MindEvidence Lab API",
        version=API_VERSION,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.google = google
    app.state.ingestion_settings = ingestion_settings or IngestionSettings()
    app.state.retrieval_settings = retrieval_settings or RetrievalSettings()
    app.state.embedder = embedder or build_embedder(app.state.retrieval_settings)
    app.state.llm_settings = llm_settings or LLMSettings()
    app.state.llm = llm_provider or DeepSeekProvider(app.state.llm_settings)
    app.state.audit = AuditLog(app.state.session_factory)
    rs = report_settings or ReportSettings()
    key = rs.email_token_encryption_key.get_secret_value()
    app.state.reports = report_service or ReportService(
        settings=rs, vault=TokenVault(key) if key else None,
        gmail=GmailClient(rs, settings.google_client_id, settings.google_client_secret.get_secret_value()),
        google=GoogleOIDCClient(client_id=settings.google_client_id,
                                client_secret=settings.google_client_secret.get_secret_value(),
                                redirect_uri=rs.gmail_redirect_uri),
        audit=app.state.audit)
    app.state.workflow = build_graph(WorkflowDeps(
        session_factory=app.state.session_factory, audit=app.state.audit, provider=app.state.llm,
        llm_settings=app.state.llm_settings, embedder=app.state.embedder,
        retrieval_settings=app.state.retrieval_settings))

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request.state.request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        try:
            response = await call_next(request)
        except Exception:  # last resort; sits inside CORSMiddleware so the browser can read the error
            logging.getLogger(__name__).exception("unhandled error")
            response = error_json(request, 500, "internal_error", "An unexpected error occurred.")
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    # Narrow CORS: exact local frontend origins, only the methods and headers the UI uses.
    # Added after the middleware above so it wraps it, which puts CORS headers on every response.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER, CSRF_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
        allow_credentials=True,  # the session cookie is sent with credentialed fetches
        max_age=600,
    )

    install_error_handlers(app)
    for router in (health.router, auth.router, patients.router, documents.router, retrieval.router,
                   answers.router, audit.router, reports.router, sessions.router, admin.router):
        app.include_router(router, prefix="/api")
    return app
