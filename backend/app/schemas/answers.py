"""POST /api/answers request/response, execution trace, and feedback."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.answering.schemas import AnswerBody, AnswerIssue, EvidenceItem, Mode, PatientFact, SafetyOut
from app.schemas.common import PatientId
from app.workflow.state import ExecutionEvent

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [{"patient_id": "P-004", "question": "What does the research say about detox alone and relapse?",
                      "mode": "rag"}]})

    patient_id: PatientId
    question: Question
    mode: Mode = Field(default="rag", description="gk = general knowledge, no retrieval; rag = research documents only; "
                                                  "both = documents plus clearly separated general knowledge; "
                                                  "auto = the model may call the search tool (capped by the backend)")
    session_id: uuid.UUID | None = Field(default=None, description="Store the exchange in this chat session (must be yours)")


class Outcome(BaseModel):
    kind: Literal["answered", "urgent", "refused", "unsupported", "no_evidence"]
    code: str | None = None
    message: str | None = Field(default=None, description="Shown instead of an answer when kind is not 'answered'")


class RetrievalOut(BaseModel):
    decision_needed: bool | None
    decision_reason: str | None
    deferred_to_model: bool = False
    performed: bool
    source: Literal["backend", "model_tool", "none"]
    settings: dict
    result_count: int
    top_similarity: float | None
    coverage: Literal["not_applicable", "none", "weak", "adequate"]
    coverage_note: str | None
    chunk_ids: list[str]
    tool_events: list[dict]
    note: str = ("Similarity scores rank passages; they are not probabilities of correctness. "
                 "Coverage is a heuristic about relatedness, not a check of truth.")


class LLMOut(BaseModel):
    used: bool
    description: dict
    calls: list[dict]
    repair_attempts: int


class AnswerResponse(BaseModel):
    correlation_id: str
    mode: Mode
    patient_id: str
    question_class: str | None
    class_reason: str | None
    outcome: Outcome
    answer: AnswerBody | None
    patient_facts: list[PatientFact] = Field(description="Facts from the SYNTHETIC case file the model was shown (F-ids)")
    evidence: list[EvidenceItem] = Field(description="Research passages the model was shown (E-ids), with full text")
    retrieval: RetrievalOut
    safety: SafetyOut
    issues: list[AnswerIssue]
    llm: LLMOut
    trace: list[ExecutionEvent]
    latency_ms: int
    source_note: str = ("Patient facts are fictional teaching material; evidence is text from published research "
                        "documents; general-knowledge statements are not backed by either.")


class TraceEvent(BaseModel):
    event_seq: int
    occurred_at: datetime
    event_type: str
    severity: str
    node: str | None
    status: str | None
    latency_ms: int | None
    metadata: dict


class TraceResponse(BaseModel):
    correlation_id: str
    events: list[TraceEvent]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correlation_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,64}$")]
    rating: Literal["helpful", "not_helpful"]
    reason: Literal["inaccurate", "missing_evidence", "unclear", "unsafe", "other"] | None = None
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class FeedbackOut(BaseModel):
    correlation_id: str
    rating: str
    reason: str | None
    has_comment: bool
