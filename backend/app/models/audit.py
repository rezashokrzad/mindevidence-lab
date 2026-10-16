"""Audit events, feedback, and email-report consent (migration 0006)."""
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Identity, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditEvent(Base):
    __tablename__ = "events"
    __table_args__ = {"schema": "audit"}

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    event_seq: Mapped[int] = mapped_column(Identity(always=True))
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    user_id: Mapped[uuid.UUID | None]
    session_id: Mapped[uuid.UUID | None]
    correlation_id: Mapped[str | None]
    event_type: Mapped[str]
    severity: Mapped[str] = mapped_column(server_default="info")
    node: Mapped[str | None]
    status: Mapped[str | None]
    latency_ms: Mapped[int | None]
    patient_id: Mapped[str | None]
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONB, server_default="{}")


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = {"schema": "audit"}

    feedback_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.user_id"))
    correlation_id: Mapped[str]
    rating: Mapped[str]
    reason: Mapped[str | None]
    comment: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class EmailConsent(Base):
    __tablename__ = "email_report_consents"
    __table_args__ = {"schema": "auth"}

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.user_id"), primary_key=True)
    status: Mapped[str]
    granted_scope: Mapped[str]
    encrypted_refresh_token: Mapped[bytes | None]
    token_key_id: Mapped[str | None]
    granted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    disabled_at: Mapped[datetime | None]
    last_attempt_at: Mapped[datetime | None]
    last_sent_at: Mapped[datetime | None]
    last_status: Mapped[str | None]
    consecutive_failures: Mapped[int] = mapped_column(server_default="0")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
