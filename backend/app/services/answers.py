"""Answer use cases: run the workflow, shape the response, persist the exchange, trace, feedback."""
from __future__ import annotations

import time
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.answering import safety
from app.audit import AuditLog
from app.errors import (
    AppError,
    AuditAccessDenied,
    LLMProblem,
    PatientNotFound,
    RetrievalUnavailable,
    SessionNotFound,
    WorkflowFailed,
)
from app.models import ChatMessage
from app.repositories import audit as audit_repo
from app.repositories import sessions as session_repo
from app.schemas.answers import (
    AnswerRequest,
    AnswerResponse,
    FeedbackOut,
    FeedbackRequest,
    LLMOut,
    Outcome,
    RetrievalOut,
    TraceEvent,
    TraceResponse,
)
from app.answering.schemas import SafetyOut
from app.security import Principal
from app.workflow.graph import run_workflow
from app.workflow.state import WorkflowState

# error code -> HTTP status for failures that end the workflow
ERROR_STATUS = {
    "retrieval_unavailable": 503, "embedding_model_mismatch": 409, "embedding_failed": 502,
    "llm_not_configured": 503, "llm_auth_failed": 502, "llm_insufficient_balance": 502, "llm_bad_request": 502,
    "llm_rate_limited": 503, "llm_unavailable": 502, "llm_timeout": 504, "llm_bad_output": 502,
    "workflow_timeout": 504, "workflow_error": 500,
}


def new_correlation_id() -> str:
    return "cid-" + uuid.uuid4().hex[:12]


def _raise_for_error(state: WorkflowState) -> None:
    t = state.terminal
    ref = f" (reference {state.correlation_id})"
    if t.code == "patient_not_found":
        raise PatientNotFound(state.patient_id)
    status = ERROR_STATUS.get(t.code or "", 500)
    if t.code == "retrieval_unavailable":
        raise RetrievalUnavailable((t.message or "Retrieval is unavailable.") + ref)
    raise LLMProblem(t.code or "workflow_error", (t.message or "The request failed.") + ref, status)


def _assistant_text(state: WorkflowState) -> str:
    t = state.terminal
    if t.kind != "answered" or not state.answer:
        return t.message or ""
    lines = []
    for c in state.answer.claims:
        cites = f" [{', '.join(c.citations)}]" if c.citations else ""
        lines.append(f"- ({c.basis.replace('_', ' ')}) {c.text}{cites}")
    for lim in state.answer.limitations:
        lines.append(f"- (limitation) {lim}")
    return "\n".join(lines)


def answer_question(db: Session, graph, audit: AuditLog, principal: Principal, body: AnswerRequest) -> AnswerResponse:
    session = None
    if body.session_id is not None:
        session = session_repo.get_owned_session(db, session_id=body.session_id, owner_user_id=principal.user_id)
        if session is None:
            raise SessionNotFound()                       # missing and not-yours look identical
        if session.patient_id != body.patient_id:
            err = AppError("This session belongs to a different patient case.")
            err.status_code, err.code = 422, "session_patient_mismatch"
            raise err

    cid = new_correlation_id()
    started = time.monotonic()
    state = run_workflow(graph, correlation_id=cid, user_id=principal.user_id, session_id=body.session_id,
                         patient_id=body.patient_id, question=body.question, mode=body.mode)
    latency = int((time.monotonic() - started) * 1000)
    t = state.terminal
    audit.record("answer.completed", user_id=principal.user_id, session_id=body.session_id, correlation_id=cid,
                 severity="error" if t.kind == "error" else "info", status="failed" if t.kind == "error" else "ok",
                 latency_ms=latency, patient_id=body.patient_id if state.patient_found else None,
                 metadata={"mode": body.mode, "outcome": t.kind, "outcome_code": t.code, "question_class": state.question_class,
                           "claims": len(state.answer.claims) if state.answer else 0,
                           "unsupported_claims": state.answer.unsupported_claim_count if state.answer else 0,
                           "retrieval_performed": state.retrieval.performed, "coverage": state.coverage,
                           "nodes": [e.node for e in state.events]})
    if t.kind == "error":
        _raise_for_error(state)

    if session is not None:
        session_repo.add_message(db, session_id=session.session_id, role="user", content=body.question,
                                 mode=body.mode, correlation_id=cid)
        session_repo.add_message(db, session_id=session.session_id, role="assistant", content=_assistant_text(state),
                                 mode=body.mode, correlation_id=cid)
        db.execute(text("UPDATE chat.sessions SET updated_at = now() WHERE session_id = :s"), {"s": session.session_id})
        db.commit()

    dec, r = state.retrieval_decision, state.retrieval
    return AnswerResponse(
        correlation_id=cid, mode=body.mode, patient_id=body.patient_id, question_class=state.question_class,
        class_reason=state.class_reason,
        outcome=Outcome(kind=t.kind, code=t.code, message=t.message if t.kind != "answered" else None),
        answer=state.answer, patient_facts=state.facts if state.patient_found else [], evidence=state.evidence,
        retrieval=RetrievalOut(
            decision_needed=dec.needed if dec else None, decision_reason=dec.reason if dec else None,
            deferred_to_model=dec.deferred_to_model if dec else False, performed=r.performed, source=r.source,
            settings=r.settings, result_count=r.result_count, top_similarity=r.top_similarity, coverage=state.coverage,
            coverage_note=state.coverage_note, chunk_ids=r.chunk_ids, tool_events=r.tool_events),
        safety=SafetyOut(urgent=state.urgent, refused=t.refusal,
                         escalation_message=safety.ESCALATION_MESSAGE if state.urgent else None,
                         clinical_disclaimer=safety.CLINICAL_DISCLAIMER),
        issues=state.issues,
        llm=LLMOut(used=bool(state.llm_calls), description=state.llm_description, calls=state.llm_calls,
                   repair_attempts=state.repair_attempts),
        trace=state.events, latency_ms=latency)


# ---------------------------------------------------------------- trace and feedback

def get_trace(db: Session, principal: Principal, correlation_id: str) -> TraceResponse:
    restrict = None if principal.role == "instructor" else principal.user_id
    rows, _ = audit_repo.list_events(db, user_id=restrict, correlation_id=correlation_id, limit=500)
    if not rows:
        raise AuditAccessDenied()          # unknown id and someone else's id are indistinguishable
    rows.sort(key=lambda e: e.event_seq)
    return TraceResponse(correlation_id=correlation_id, events=[
        TraceEvent(event_seq=e.event_seq, occurred_at=e.occurred_at, event_type=e.event_type, severity=e.severity,
                   node=e.node, status=e.status, latency_ms=e.latency_ms, metadata=e.event_metadata) for e in rows])


def submit_feedback(db: Session, audit: AuditLog, principal: Principal, body: FeedbackRequest) -> FeedbackOut:
    if not audit_repo.user_has_correlation(db, principal.user_id, body.correlation_id):
        raise AuditAccessDenied()          # can only rate your own answers
    audit_repo.upsert_feedback(db, user_id=principal.user_id, correlation_id=body.correlation_id, rating=body.rating,
                               reason=body.reason, comment=body.comment or None)
    db.commit()
    audit.record("feedback.submitted", user_id=principal.user_id, correlation_id=body.correlation_id,
                 severity="warning" if body.reason == "unsafe" else "info", status="ok",
                 metadata={"rating": body.rating, "reason": body.reason, "has_comment": bool(body.comment),
                           "comment_length": len(body.comment or "")})
    return FeedbackOut(correlation_id=body.correlation_id, rating=body.rating, reason=body.reason,
                       has_comment=bool(body.comment))
