"""Configuration from environment variables (optionally loaded from backend/.env).

Credentials never live in code or the repository: copy .env.example to .env
(git-ignored) or export the variables in your shell. Secrets are SecretStr so
they are never printed in reprs, logs, or error messages.
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env", override=False)

API_VERSION = "0.4.0"

_PLACEHOLDER_MARKERS = ("change_me", "changeme", "replace", "example", "your-")


class ConfigError(RuntimeError):
    pass


def normalize_db_url(url: str) -> str:
    url = url.strip()
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def database_url(var: str = "DATABASE_URL") -> str:
    """Return the SQLAlchemy URL stored in the given environment variable (used by Alembic and the seed)."""
    url = os.environ.get(var, "").strip()
    if not url:
        raise ConfigError(
            f"{var} is not set. Copy backend/.env.example to backend/.env and fill it in, "
            f"or export {var} in your shell."
        )
    return normalize_db_url(url)


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class Settings(BaseSettings):
    """API settings. Every field maps to an upper-case environment variable."""

    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    database_url: str = Field(description="SQLAlchemy URL of the application database")
    app_env: Literal["development", "test", "production"] = "development"

    # Exact frontend origins allowed by CORS and by the CSRF Origin check (no wildcards).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Where the browser lands after sign-in when no valid return_to is given.
    frontend_url: str = "http://localhost:5173/"

    # ---- Google OAuth 2.0 / OpenID Connect (server-side authorization-code flow) ----
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # ---- Sessions ----
    # Signs the short-lived OAuth state cookie and keys the HMAC of session tokens.
    session_secret: SecretStr = Field(description="At least 32 random characters")
    session_ttl_hours: int = Field(default=12, ge=1, le=24 * 14)
    # Secure cookies need HTTPS. Must be true in production. Browsers treat http://localhost
    # as a secure context, but false is allowed in development for portability.
    cookie_secure: bool = False

    # ---- Who may sign in, and who is an instructor ----
    # Empty lists = any Google account with a verified email may sign in.
    allowed_email_domains: str = ""   # e.g. "university.edu" (checked against the verified email and hd claim)
    allowed_emails: str = ""          # explicit addresses, case-insensitive
    instructor_google_subs: str = ""  # Google `sub` ids that get the instructor role (never by email)

    db_pool_size: int = Field(default=5, ge=1, le=50)

    @field_validator("database_url")
    @classmethod
    def _normalize(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("DATABASE_URL must not be empty")
        return normalize_db_url(v)

    @field_validator("session_secret")
    @classmethod
    def _strong_secret(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if len(raw) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters")
        if any(m in raw.lower() for m in _PLACEHOLDER_MARKERS):
            raise ValueError("SESSION_SECRET still looks like a placeholder; generate a random value")
        return v

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must list exact origins; '*' is not allowed")
        if _origin(self.frontend_url) not in self.cors_origin_list:
            raise ValueError("FRONTEND_URL must be on one of the CORS_ORIGINS")
        if self.app_env == "production":
            problems = []
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true")
            for name, url in [("GOOGLE_REDIRECT_URI", self.google_redirect_uri), ("FRONTEND_URL", self.frontend_url),
                              *[("CORS_ORIGINS", o) for o in self.cors_origin_list]]:
                if not url.startswith("https://"):
                    problems.append(f"{name} must use https:// ({url})")
            if not self.google_client_id or not self.google_client_secret.get_secret_value():
                problems.append("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required")
            if problems:
                raise ValueError("Production configuration is unsafe: " + "; ".join(problems))
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.rstrip("/") for o in _csv(self.cors_origins)]

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret.get_secret_value())

    @property
    def allowed_domain_list(self) -> list[str]:
        return [d.lower().lstrip("@") for d in _csv(self.allowed_email_domains)]

    @property
    def allowed_email_list(self) -> list[str]:
        return [e.lower() for e in _csv(self.allowed_emails)]

    @property
    def instructor_sub_list(self) -> list[str]:
        return _csv(self.instructor_google_subs)

    @property
    def session_cookie_name(self) -> str:
        # The __Host- prefix makes browsers enforce Secure, Path=/ and no Domain attribute.
        return "__Host-me_session" if self.cookie_secure else "me_session"


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # Never echo input values: they may contain secrets.
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'settings'}: {e['msg']}"
                             for e in exc.errors(include_input=False, include_url=False))
        raise ConfigError(f"Invalid configuration: {problems}") from None
