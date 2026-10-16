"""Users and login sessions (schema `auth`, migration 0003)."""
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "auth"}

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    google_sub: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str]
    email_verified: Mapped[bool]
    display_name: Mapped[str | None]
    role: Mapped[str] = mapped_column(server_default="student")
    is_active: Mapped[bool] = mapped_column(server_default="true")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_login_at: Mapped[datetime | None]


class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = {"schema": "auth"}

    login_session_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.user_id"))
    token_hash: Mapped[bytes] = mapped_column(unique=True)
    csrf_token: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(lazy="joined")
