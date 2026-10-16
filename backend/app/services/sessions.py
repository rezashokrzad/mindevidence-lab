"""Chat-session use cases. Every operation is scoped to the signed-in user (ownership checks)."""
import uuid

from sqlalchemy.orm import Session

from app.errors import SessionNotFound, UnknownPatientReference
from app.repositories import patients as patient_repo
from app.repositories import sessions as repo
from app.schemas.sessions import MessageOut, MessagePage, SessionCreate, SessionOut, SessionPage
from app.security import Principal


def create_session(db: Session, principal: Principal, body: SessionCreate) -> SessionOut:
    # Cross-schema reference: validated here instead of by a foreign key (see migration 0002).
    if not patient_repo.patient_exists(db, body.patient_id):
        raise UnknownPatientReference(body.patient_id)
    row = repo.create_session(db, owner_user_id=principal.user_id, patient_id=body.patient_id,
                              title=body.title, mode=body.mode)
    db.commit()
    return _to_out(row, message_count=0)


def list_sessions(db: Session, principal: Principal, *, patient_id: str | None,
                  limit: int, offset: int) -> SessionPage:
    rows, total = repo.list_sessions(db, owner_user_id=principal.user_id, patient_id=patient_id,
                                     limit=limit, offset=offset)
    return SessionPage(items=[_to_out(s, message_count=n) for s, n in rows], total=total, limit=limit, offset=offset)


def list_messages(db: Session, principal: Principal, session_id: uuid.UUID, *,
                  limit: int, offset: int) -> MessagePage:
    # Ownership check: a session owned by someone else is indistinguishable from a missing one.
    session = repo.get_owned_session(db, session_id=session_id, owner_user_id=principal.user_id)
    if session is None:
        raise SessionNotFound()
    rows, total = repo.list_messages(db, session_id=session_id, limit=limit, offset=offset)
    return MessagePage(session_id=session_id, items=[MessageOut.model_validate(m) for m in rows],
                       total=total, limit=limit, offset=offset)


def _to_out(s, *, message_count: int) -> SessionOut:
    return SessionOut(session_id=s.session_id, patient_id=s.patient_id, title=s.title, mode=s.mode,
                      message_count=message_count, created_at=s.created_at, updated_at=s.updated_at)
