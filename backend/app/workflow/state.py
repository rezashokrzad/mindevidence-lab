"""Typed workflow state (Pydantic v2) for the question-answering graph.

Patient facts and research evidence are separate fields and stay separate through the whole workflow.
`events` is the execution trace: which nodes ran, what each decided and why (concise reasons only, never
model reasoning), and how long it took.
"""
from __future__ import annotations

import operator
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.answering.schemas import (
    AnswerBody,
    AnswerIssue,
    EvidenceItem,
    Mode,
    ModelAnswer,
    PatientFact,
)

QuestionClass = Literal["patient_history_lookup", "research_question", "comparison", "unsupported"]
TerminalKind = Literal["answered", "urgent", "refused", "unsupported", "no_evidence", "error"]
NodeStatus = Literal["ok", "skipped", "failed", "blocked"]
Coverage = Literal["not_applicable", "none", "weak", "adequate"]


class ExecutionEvent(BaseModel):
    """One executed node. `reason` is a short, human-readable decision reason (never chain-of-thought)."""

    seq: int
    node: str
    status: NodeStatus
    reason: str
    latency_ms: int
    details: dict = Field(default_factory=dict)


class RetrievalDecision(BaseModel):
    needed: bool
    reason: str
    deferred_to_model: bool = False        # auto mode: the model may call the search tool (capped by the backend)


class RetrievalRecord(BaseModel):
    performed: bool = False
    source: Literal["backend", "model_tool", "none"] = "none"
    settings: dict = Field(default_factory=dict)
    result_count: int = 0
    top_similarity: float | None = None
    chunk_ids: list[str] = Field(default_factory=list)
    tool_events: list[dict] = Field(default_factory=list)


class Terminal(BaseModel):
    kind: TerminalKind
    message: str | None = None
    code: str | None = None            # machine-readable error / reason code
    refusal: Literal["diagnosis", "prescription", "treatment_selection"] | None = None


class WorkflowState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    started_monotonic: float = 0.0     # time budget reference (set by run_workflow)

    # ---- inputs
    correlation_id: str
    user_id: uuid.UUID
    session_id: uuid.UUID | None = None
    patient_id: str
    question: str
    mode: Mode

    # ---- patient (synthetic record) -- kept separate from research evidence
    patient_found: bool = False
    facts: list[PatientFact] = Field(default_factory=list)

    # ---- classification and routing
    urgent: bool = False
    question_class: QuestionClass | None = None
    class_reason: str | None = None
    retrieval_decision: RetrievalDecision | None = None

    # ---- research evidence
    retrieval: RetrievalRecord = Field(default_factory=RetrievalRecord)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    coverage: Coverage = "not_applicable"
    coverage_note: str | None = None

    # ---- generation and validation
    llm_calls: list[dict] = Field(default_factory=list)
    repair_attempts: int = 0
    model_answer: ModelAnswer | None = None
    answer: AnswerBody | None = None
    issues: list[AnswerIssue] = Field(default_factory=list)

    # ---- outcome
    terminal: Terminal | None = None
    events: Annotated[list[ExecutionEvent], operator.add] = Field(default_factory=list)
    llm_description: dict = Field(default_factory=dict)
