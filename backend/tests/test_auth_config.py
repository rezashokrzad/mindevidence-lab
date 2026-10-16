"""Configuration safety and secret hygiene. No database required."""
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ConfigError, Settings, get_settings

STRONG = "Zq8" + "x" * 45

BASE = {"database_url": "postgresql+psycopg://u@localhost/db", "session_secret": STRONG}

PROD_OK = {
    **BASE, "app_env": "production", "cookie_secure": True,
    "google_client_id": "id.apps.googleusercontent.com", "google_client_secret": "s3cr3t",
    "google_redirect_uri": "https://lab.example.edu/api/auth/callback",
    "frontend_url": "https://lab.example.edu/", "cors_origins": "https://lab.example.edu",
}


def test_production_config_accepted():
    s = Settings(**PROD_OK)
    assert s.session_cookie_name == "__Host-me_session"


@pytest.mark.parametrize("override, message", [
    ({"cookie_secure": False}, "COOKIE_SECURE"),
    ({"google_redirect_uri": "http://lab.example.edu/api/auth/callback"}, "GOOGLE_REDIRECT_URI"),
    ({"frontend_url": "http://lab.example.edu/", "cors_origins": "http://lab.example.edu"}, "https"),
    ({"google_client_secret": ""}, "GOOGLE_CLIENT_SECRET"),
])
def test_production_refuses_unsafe_config(override, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**{**PROD_OK, **override})


@pytest.mark.parametrize("secret", ["short", "CHANGE_ME" + "x" * 40, "replace-with-random-" + "y" * 30])
def test_weak_or_placeholder_session_secret_refused(secret):
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "session_secret": secret})


def test_frontend_url_must_be_an_allowed_origin():
    with pytest.raises(ValidationError, match="FRONTEND_URL"):
        Settings(**{**BASE, "frontend_url": "http://localhost:3000/"})


def test_startup_error_never_echoes_secret(monkeypatch):
    weak = "weak-but-secret-value"
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@localhost/db")
    monkeypatch.setenv("SESSION_SECRET", weak)
    get_settings.cache_clear()
    with pytest.raises(ConfigError) as exc:
        get_settings()
    get_settings.cache_clear()
    assert weak not in str(exc.value) and "SESSION_SECRET" in str(exc.value)


def test_secrets_hidden_in_repr():
    s = Settings(**PROD_OK)
    assert "s3cr3t" not in repr(s) and STRONG not in repr(s)


def test_frontend_contains_no_secrets_or_secret_names():
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    pattern = re.compile(r"client_secret|SESSION_SECRET|GOOGLE_CLIENT_SECRET|GOCSPX-", re.I)
    offenders = [str(p) for p in frontend.rglob("*") if p.is_file() and p.suffix in {".js", ".html", ".css"}
                 and pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_env_example_has_no_real_secrets():
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in example.splitlines() if "=" in line and not line.startswith("#"))
    assert values["GOOGLE_CLIENT_SECRET"] == ""
    assert values["SESSION_SECRET"] == ""
    assert not values["GOOGLE_CLIENT_ID"].strip() or "example" in values["GOOGLE_CLIENT_ID"]
