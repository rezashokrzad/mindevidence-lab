import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import API_VERSION, Settings
from app.repositories import health as repo
from app.schemas.health import HealthOut

log = logging.getLogger(__name__)


def check(db: Session, settings: Settings) -> HealthOut:
    try:
        version = repo.schema_version(db)
        database = "ok"
    except SQLAlchemyError as exc:
        log.warning("health check: database unavailable (%s)", type(exc).__name__)
        version, database = None, "unavailable"
    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        database=database,
        schema_version=version,
        version=API_VERSION,
        environment=settings.app_env,
        sign_in="google" if settings.google_configured else "not_configured",
    )
