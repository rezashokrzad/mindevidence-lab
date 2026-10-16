"""Database access for users and login sessions."""
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import LoginSession, User


def upsert_google_user(db: Session, *, google_sub: str, email: str, email_verified: bool,
                       display_name: str | None, role: str, now: datetime) -> User:
    """Create or update the user identified by Google `sub`. Email is updated, never used as the key."""
    stmt = (
        insert(User)
        .values(google_sub=google_sub, email=email, email_verified=email_verified,
                display_name=display_name, role=role, last_login_at=now)
        .on_conflict_do_update(
            index_elements=[User.google_sub],
            set_={"email": email, "email_verified": email_verified, "display_name": display_name,
                  "role": role, "last_login_at": now},
        )
        .returning(User.user_id)
    )
    user_id = db.execute(stmt).scalar_one()
    return db.get(User, user_id, populate_existing=True)


def get_user(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def create_login_session(db: Session, *, user_id: uuid.UUID, token_hash: bytes, csrf_token: str,
                         expires_at: datetime) -> LoginSession:
    row = LoginSession(user_id=user_id, token_hash=token_hash, csrf_token=csrf_token, expires_at=expires_at)
    db.add(row)
    db.flush()
    return row


def find_active_session(db: Session, token_hash: bytes) -> LoginSession | None:
    """A session that is not revoked, not expired, and whose user is still active."""
    return db.scalars(
        select(LoginSession)
        .join(User, User.user_id == LoginSession.user_id)
        .where(LoginSession.token_hash == token_hash,
               LoginSession.revoked_at.is_(None),
               LoginSession.expires_at > func.now(),
               User.is_active.is_(True))
    ).first()


def touch_session(db: Session, login_session_id: uuid.UUID) -> None:
    db.execute(update(LoginSession).where(LoginSession.login_session_id == login_session_id)
               .values(last_seen_at=func.now()))


def revoke_session(db: Session, token_hash: bytes) -> int:
    result = db.execute(
        update(LoginSession)
        .where(LoginSession.token_hash == token_hash, LoginSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    return result.rowcount


def purge_stale_sessions(db: Session, user_id: uuid.UUID, older_than: timedelta = timedelta(days=7)) -> None:
    """Housekeeping on sign-in: drop this user's sessions that expired or were revoked a while ago."""
    db.execute(delete(LoginSession).where(
        LoginSession.user_id == user_id,
        func.coalesce(LoginSession.revoked_at, LoginSession.expires_at) < func.now() - older_than,
    ))
