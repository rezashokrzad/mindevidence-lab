"""ORM models mapped onto the schema created by the SQL migrations.

Migrations (db/sql via Alembic) are the source of truth; these classes only map
existing tables and are never used to create them.
"""
from app.models.audit import AuditEvent, EmailConsent, Feedback
from app.models.auth import LoginSession, User
from app.models.base import Base
from app.models.cases import (
    CoOccurringSymptom,
    HistoryEvent,
    Intervention,
    Patient,
    ProgressObservation,
    RiskFactor,
    SubstanceOrBehaviorUse,
)
from app.models.chat import ChatMessage, ChatSession
from app.models.corpus import Chunk, Document, IngestionRun

__all__ = [
    "Base", "Patient", "SubstanceOrBehaviorUse", "Intervention", "HistoryEvent", "RiskFactor",
    "ProgressObservation", "CoOccurringSymptom", "Document", "Chunk", "IngestionRun", "ChatSession", "ChatMessage", "User", "LoginSession", "AuditEvent", "Feedback", "EmailConsent",
]
