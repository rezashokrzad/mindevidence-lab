import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.common import ApiModel, Mode, Page, PatientId

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class SessionCreate(BaseModel):
    """Start a research chat session about one synthetic patient."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [{"patient_id": "P-004", "title": "Maintenance vs detox", "mode": "rag"}]})

    patient_id: PatientId
    title: Title | None = None
    mode: Mode = "rag"


class SessionOut(ApiModel):
    session_id: uuid.UUID
    patient_id: str
    title: str | None
    mode: Mode
    message_count: int
    created_at: datetime
    updated_at: datetime


class SessionPage(Page[SessionOut]):
    pass


class MessageOut(ApiModel):
    message_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    mode: Mode
    correlation_id: str | None
    created_at: datetime


class MessagePage(Page[MessageOut]):
    session_id: uuid.UUID
