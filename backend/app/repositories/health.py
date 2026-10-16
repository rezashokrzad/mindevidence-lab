"""Database probes for the health endpoint."""
from sqlalchemy import text
from sqlalchemy.orm import Session


def schema_version(db: Session) -> str | None:
    """Return the applied Alembic revision. Raises if the database is unreachable."""
    db.execute(text("SELECT 1"))
    return db.execute(text("SELECT version_num FROM public.alembic_version LIMIT 1")).scalar()
