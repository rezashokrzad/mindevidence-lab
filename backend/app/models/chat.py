"""Chat sessions and messages (schema `chat`, migration 0002)."""
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Identity, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatSession(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "chat"}

    session_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.user_id"))
    patient_id: Mapped[str]
    title: Mapped[str | None]
    mode: Mapped[str] = mapped_column(server_default="rag")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "messages"
    __table_args__ = {"schema": "chat"}

    message_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    # GENERATED ALWAYS AS IDENTITY in the database; never written by the application.
    message_seq: Mapped[int] = mapped_column(Identity(always=True))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat.sessions.session_id"))
    role: Mapped[str]
    content: Mapped[str]
    mode: Mapped[str]
    correlation_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
