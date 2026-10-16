"""Database access for chat sessions and messages.

Every query is scoped by owner_user_id; the service layer passes the signed-in user.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ChatMessage, ChatSession


def create_session(db: Session, *, owner_user_id: uuid.UUID, patient_id: str, title: str | None,
                   mode: str) -> ChatSession:
    row = ChatSession(owner_user_id=owner_user_id, patient_id=patient_id, title=title, mode=mode)
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def _message_count():
    return (select(func.count(ChatMessage.message_id))
            .where(ChatMessage.session_id == ChatSession.session_id)
            .correlate(ChatSession).scalar_subquery())


def list_sessions(db: Session, *, owner_user_id: uuid.UUID, patient_id: str | None,
                  limit: int, offset: int) -> tuple[list[tuple[ChatSession, int]], int]:
    where = [ChatSession.owner_user_id == owner_user_id]
    if patient_id:
        where.append(ChatSession.patient_id == patient_id)
    total = db.scalar(select(func.count()).select_from(ChatSession).where(*where))
    rows = db.execute(
        select(ChatSession, _message_count().label("message_count"))
        .where(*where)
        .order_by(ChatSession.created_at.desc(), ChatSession.session_id)
        .limit(limit).offset(offset)
    ).all()
    return [(r[0], r[1]) for r in rows], total or 0


def get_owned_session(db: Session, *, session_id: uuid.UUID, owner_user_id: uuid.UUID) -> ChatSession | None:
    return db.scalars(
        select(ChatSession).where(ChatSession.session_id == session_id, ChatSession.owner_user_id == owner_user_id)
    ).first()


def count_messages(db: Session, session_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).where(ChatMessage.session_id == session_id)) or 0


def list_messages(db: Session, *, session_id: uuid.UUID, limit: int, offset: int) -> tuple[list[ChatMessage], int]:
    total = count_messages(db, session_id)
    rows = db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.message_seq).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def add_message(db: Session, *, session_id: uuid.UUID, role: str, content: str, mode: str,
                correlation_id: str | None) -> ChatMessage:
    row = ChatMessage(session_id=session_id, role=role, content=content or "(no text)", mode=mode,
                      correlation_id=correlation_id)
    db.add(row)
    db.flush()
    return row
