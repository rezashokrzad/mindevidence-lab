"""Reads of audit events and feedback writes. Events themselves are written by app.audit.AuditLog."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import AuditEvent, Feedback


def list_events(db: Session, *, user_id: uuid.UUID | None, correlation_id: str | None = None,
                session_id: uuid.UUID | None = None, event_type: str | None = None,
                min_severity: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[AuditEvent], int]:
    where = []
    if user_id is not None:                      # None = unrestricted (instructors only; the service decides)
        where.append(AuditEvent.user_id == user_id)
    if correlation_id:
        where.append(AuditEvent.correlation_id == correlation_id)
    if session_id:
        where.append(AuditEvent.session_id == session_id)
    if event_type:
        where.append(AuditEvent.event_type.like(event_type.replace("%", "") + "%"))
    if min_severity == "warning":
        where.append(AuditEvent.severity.in_(["warning", "error"]))
    elif min_severity == "error":
        where.append(AuditEvent.severity == "error")
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*where))
    rows = db.scalars(select(AuditEvent).where(*where).order_by(AuditEvent.event_seq.desc())
                      .limit(limit).offset(offset)).all()
    return list(rows), total or 0


def user_has_correlation(db: Session, user_id: uuid.UUID, correlation_id: str) -> bool:
    return bool(db.scalar(select(func.count()).where(AuditEvent.user_id == user_id,
                                                      AuditEvent.correlation_id == correlation_id)))


def upsert_feedback(db: Session, *, user_id: uuid.UUID, correlation_id: str, rating: str, reason: str | None,
                    comment: str | None) -> None:
    stmt = pg_insert(Feedback).values(user_id=user_id, correlation_id=correlation_id, rating=rating, reason=reason,
                                      comment=comment)
    db.execute(stmt.on_conflict_do_update(
        constraint="feedback_one_per_answer",
        set_={"rating": rating, "reason": reason, "comment": comment, "created_at": func.now()}))
