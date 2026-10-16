"""Audit-event API schemas and the activity-dashboard summary."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.common import Page, PatientId


class AuditEventOut(BaseModel):
    event_id: uuid.UUID
    event_seq: int
    occurred_at: datetime
    user_id: uuid.UUID | None
    session_id: uuid.UUID | None
    correlation_id: str | None
    event_type: str
    severity: Literal["info", "warning", "error"]
    node: str | None
    status: str | None
    latency_ms: int | None
    patient_id: str | None
    metadata: dict


class AuditEventPage(Page[AuditEventOut]):
    scope: Literal["own", "all"]


class UiEventRequest(BaseModel):
    """Client-side events the backend accepts. Anything else is rejected: the audit trail is not a free-form log."""

    model_config = ConfigDict(extra="forbid")

    event_type: Literal["patient.selected", "mode.changed", "citation.opened"]
    patient_id: PatientId | None = None
    mode: Literal["gk", "rag", "both", "auto"] | None = None
    correlation_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,64}$")] | None = None
    chunk_id: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9-]*-c[0-9]{4}$")] | None = None

    @model_validator(mode="after")
    def _required_fields(self) -> "UiEventRequest":
        need = {"patient.selected": ["patient_id"], "mode.changed": ["mode"],
                "citation.opened": ["correlation_id", "chunk_id"]}[self.event_type]
        missing = [f for f in need if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.event_type} requires {', '.join(missing)}")
        return self


class UiEventOut(BaseModel):
    recorded: bool


class NodeSummary(BaseModel):
    node: str
    status: str | None
    latency_ms: int | None
    reason: str | None


class AnswerActivity(BaseModel):
    correlation_id: str
    started_at: datetime
    mode: str | None
    outcome: str | None
    outcome_code: str | None
    question_class: str | None
    patient_id: str | None
    latency_ms: int | None
    total_node_ms: int
    claims: int | None
    unsupported_claims: int | None
    retrieval: dict | None = Field(description="Retrieval trace: settings, result count, top similarity, chunk ids")
    model: dict | None = Field(description="Model call metadata for this answer (model, calls, tokens, latency)")
    nodes: list[NodeSummary]
    errors: list[str]
    feedback: str | None = None


class ActivityTotals(BaseModel):
    answers: int
    answered: int
    refused_or_unsupported: int
    errors: int
    retrievals: int
    unsupported_claims: int
    median_latency_ms: int | None
    max_latency_ms: int | None


class ActivityResponse(BaseModel):
    since: datetime
    login_session_started_at: datetime
    totals: ActivityTotals
    model_config_in_use: dict | None = Field(description="Non-secret model configuration seen most recently")
    answers: list[AnswerActivity]
    events: list[AuditEventOut]
    errors: list[AuditEventOut]
