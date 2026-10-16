"""Workflow nodes. Each node returns a `NodeOutcome`; `traced()` turns that into state updates, an execution
event, and an audit event, and enforces the time budget. Unexpected exceptions end the run safely.

Database work and model calls happen here (never in the UI). Document text and patient data are treated as
untrusted DATA: they are passed to the model inside labelled delimiters and can never trigger a tool call; the
backend decides which tools exist, validates tool arguments, and caps their use.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.answering import safety
from app.answering.context import evidence_from_results, facts_from_patient
from app.answering.generator import generate_answer
from app.answering.schemas import EvidenceItem
from app.answering.validation import validate_answer
from app.audit import AuditLog
from app.errors import AppError, PatientNotFound
from app.llm.config import LLMSettings
from app.llm.provider import LLMError, LLMProvider
from app.retrieval.config import RetrievalSettings
from app.retrieval.embedder import Embedder
from app.schemas.retrieval import SearchFilters, SearchRequest
from app.services import patients as patient_service
from app.services import retrieval as retrieval_service
from app.workflow.classify import classify_question
from app.workflow.state import (
    ExecutionEvent,
    RetrievalDecision,
    RetrievalRecord,
    Terminal,
    WorkflowState,
)

log = logging.getLogger(__name__)

# Heuristic similarity floors for the MiniLM model. They are NOT probabilities and do not measure correctness;
# they only separate "essentially unrelated" from "possibly related" (see docs/retrieval.md).
NOISE_FLOOR = 0.30
WEAK_FLOOR = 0.45


@dataclass
class WorkflowDeps:
    session_factory: sessionmaker[Session]
    audit: AuditLog
    provider: LLMProvider
    llm_settings: LLMSettings
    embedder: Embedder
    retrieval_settings: RetrievalSettings
    timeout_seconds: float = 150.0
    noise_floor: float = NOISE_FLOOR
    weak_floor: float = WEAK_FLOOR


@dataclass
class NodeOutcome:
    updates: dict = field(default_factory=dict)
    status: str = "ok"                    # ok | skipped | failed | blocked
    reason: str = ""
    details: dict = field(default_factory=dict)
    severity: str = "info"


NodeFn = Callable[[WorkflowState, WorkflowDeps], NodeOutcome]


def traced(name: str, fn: NodeFn, deps: WorkflowDeps) -> Callable[[WorkflowState], dict]:
    def run(state: WorkflowState) -> dict:
        started = time.monotonic()
        seq = len(state.events) + 1
        if state.terminal is not None and name != "finalize":
            outcome = NodeOutcome(status="skipped", reason=f"run already ended ({state.terminal.kind})")
        elif state.started_monotonic and time.monotonic() - state.started_monotonic > deps.timeout_seconds:
            outcome = NodeOutcome(
                updates={"terminal": Terminal(kind="error", code="workflow_timeout",
                                              message="The request took too long and was stopped.")},
                status="failed", reason="workflow time budget exceeded", severity="error")
        else:
            try:
                outcome = fn(state, deps)
            except Exception as exc:
                log.exception("workflow node %s crashed", name)
                outcome = NodeOutcome(
                    updates={"terminal": Terminal(kind="error", code="workflow_error",
                                                  message="An unexpected error stopped this request.")},
                    status="failed", reason=f"unexpected {type(exc).__name__}", severity="error")
        latency = int((time.monotonic() - started) * 1000)
        event = ExecutionEvent(seq=seq, node=name, status=outcome.status, reason=outcome.reason,
                               latency_ms=latency, details=outcome.details)
        deps.audit.record(
            "workflow.node", user_id=state.user_id, session_id=state.session_id, correlation_id=state.correlation_id,
            severity=outcome.severity, node=name, status=outcome.status, latency_ms=latency,
            patient_id=state.patient_id if state.patient_found or name == "validate_input" else None,
            metadata={"reason": outcome.reason, **outcome.details})
        return {**outcome.updates, "events": [event]}

    run.__name__ = f"node_{name}"
    return run


# ------------------------------------------------------------------ 1. validate
def validate_input(state: WorkflowState, deps: WorkflowDeps) -> NodeOutcome:
    deps.audit.record("question.submitted", user_id=state.user_id, session_id=state.session_id,
                      correlation_id=state.correlation_id, patient_id=state.patient_id,
                      metadata={"mode": state.mode, "question_length": len(state.question),
                                "question_words": len(state.question.split())})
    try:
        with deps.session_factory() as db:
            patient = patient_service.get_patient(db, state.patient_id)
    except PatientNotFound:
        return NodeOutcome(
            updates={"terminal": Terminal(kind="error", code="patient_not_found",
                                          message=f"No synthetic patient with id {state.patient_id}.")},
            status="failed", reason="selected patient does not exist", severity="warning")
    facts = facts_from_patient(patient)
    return NodeOutcome(updates={"patient_found": True, "facts": facts}, reason="patient found; question and mode valid",
                       details={"patient_fact_count": len(facts)})


# ------------------------------------------------------------------ 2. classify
def classify(state: WorkflowState, deps: WorkflowDeps) -> NodeOutcome:
    safe = safety.assess_question(state.question)
    if safe.urgent:
        return NodeOutcome(
            updates={"urgent": True, "terminal": Terminal(kind="urgent", message=safety.ESCALATION_MESSAGE,
                                                          code="urgent_wording")},
            status="blocked", reason="urgent or dangerous wording: escalation message returned, no model call",
            details={"matched_rule": "urgent"}, severity="warning")
    if safe.refusal:
        return NodeOutcome(
            updates={"terminal": Terminal(kind="refused", refusal=safe.refusal, code=f"refused_{safe.refusal}",
                                          message=safety.REFUSAL_MESSAGES[safe.refusal])},
            status="blocked", reason=f"request to {safe.refusal.replace('_', ' ')} is outside this tool's scope",
            details={"refusal": safe.refusal}, severity="warning")
    cls, rule = classify_question(state.question)
    updates: dict = {"question_class": cls, "class_reason": rule}
    if cls == "unsupported":
        updates["terminal"] = Terminal(
            kind="unsupported", code=rule,
            message=("This tool answers questions about addiction treatment research and the synthetic patient cases. "
                     "That question is outside its scope." if rule != "multi_patient_comparison_not_supported" else
                     "Questions that compare several patients are not supported: select one case at a time and "
                     "ask how its history relates to the research."))
        return NodeOutcome(updates=updates, status="blocked", reason=f"unsupported request ({rule})",
                           details={"class": cls, "rule": rule})
    return NodeOutcome(updates=updates, reason=f"classified as {cls} by rule {rule}", details={"class": cls, "rule": rule})


# ------------------------------------------------------------------ 3. decide retrieval
def decide_retrieval(state: WorkflowState, deps: WorkflowDeps) -> NodeOutcome:
    cls = state.question_class
    if state.mode == "gk":
        d = RetrievalDecision(needed=False, reason="General Knowledge mode never searches the research documents")
    elif cls == "patient_history_lookup":
        d = RetrievalDecision(needed=False, reason="the question can be answered from the case record alone")
    elif state.mode == "auto":
        d = RetrievalDecision(needed=True, deferred_to_model=True,
                              reason=f"auto mode on a {cls}: the model may call the search tool (capped by the backend)")
    else:
        d = RetrievalDecision(needed=True, reason=f"{state.mode} mode and the question is a {cls}")
    return NodeOutcome(updates={"retrieval_decision": d}, reason=d.reason,
                       details={"needed": d.needed, "deferred_to_model": d.deferred_to_model, "mode": state.mode})


# ------------------------------------------------------------------ 4. retrieve
def retrieve(state: WorkflowState, deps: WorkflowDeps) -> NodeOutcome:
    rs = deps.retrieval_settings
    started = time.monotonic()
    try:
        with deps.session_factory() as db:
            resp = retrieval_service.run_search(db, deps.embedder, rs, SearchRequest(query=state.question,
                                                                                   top_k=rs.retrieval_default_top_k))
    except AppError as exc:
        deps.audit.record("retrieval.executed", user_id=state.user_id, session_id=state.session_id,
                          correlation_id=state.correlation_id, severity="error", status="failed",
                          metadata={"error_code": exc.code})
        return NodeOutcome(updates={"terminal": Terminal(kind="error", code=exc.code, message=exc.message)},
                           status="failed", reason=f"retrieval failed ({exc.code})", severity="error")
    evidence = evidence_from_results(resp.results)
    top = evidence[0].similarity if evidence else None
    settings = resp.settings.model_dump()
    record = RetrievalRecord(performed=True, source="backend", settings=settings, result_count=len(evidence),
                             top_similarity=top, chunk_ids=[e.chunk_id for e in evidence])
    deps.audit.record(
        "retrieval.executed", user_id=state.user_id, session_id=state.session_id, correlation_id=state.correlation_id,
        status="ok", latency_ms=int((time.monotonic() - started) * 1000),
        metadata={"settings": settings, "result_count": len(evidence), "top_similarity": top,
                  "chunk_ids": record.chunk_ids, "similarities": [e.similarity for e in evidence]})
    return NodeOutcome(updates={"evidence": evidence, "retrieval": record}, reason=f"retrieved {len(evidence)} chunk(s)",
                       details={"result_count": len(evidence), "top_similarity": top, "model": settings["embedding_model"]})


# ------------------------------------------------------------------ 5. assess evidence
def assess_evidence(state: WorkflowState, deps: WorkflowDeps) -> NodeOutcome:
    dec = state.retrieval_decision
    if not state.retrieval.performed:
        why = ("retrieval is left to the model's search tool" if dec and dec.deferred_to_model
               else "no retrieval was needed")
        return NodeOutcome(updates={"coverage": "not_applicable"}, status="skipped", reason=why)
    top = state.retrieval.top_similarity
    if not state.evidence or (top is not None and top < deps.noise_floor):
        note = ("no passages were retrieved" if not state.evidence else
                f"the closest passage is essentially unrelated (similarity {top:.2f}, below the {deps.noise_floor:.2f} floor)")
        updates: dict = {"coverage": "none", "coverage_note": note, "evidence": []}
        if state.mode == "rag":
            updates["terminal"] = Terminal(
                kind="no_evidence", code="no_relevant_evidence",
                message=("The research documents did not return a passage related to this question, so no "
                         "evidence-based answer can be given. Try rephrasing the question, or switch to Both or "
                         "General Knowledge mode for uncited background."))
        return NodeOutcome(updates=updates, status="blocked" if state.mode == "rag" else "ok",
                           reason=f"coverage none: {note}", details={"coverage": "none", "top_similarity": top})
    if top is not None and top < deps.weak_floor:
        note = (f"the closest passages are only weakly similar to the question (top similarity {top:.2f}); "
                "they may not address it. State this in limitations instead of stretching them.")
        return NodeOutcome(updates={"coverage": "weak", "coverage_note": note}, reason="coverage weak",
                           details={"coverage": "weak", "top_similarity": top})
    return NodeOutcome(updates={"coverage": "adequate"}, reason="passages above the similarity floors (not a correctness check)",
                       details={"coverage": "adequate", "top_similarity": top})


# ------------------------------------------------------------------ 6. generate
def generate(state: WorkflowState, deps: WorkflowDeps) -> NodeOutcome:
    rs = deps.retrieval_settings
    tool_hits: list[EvidenceItem] = []

    def search(query: str, topics: list[str] | None, start: int) -> list[EvidenceItem]:
        with deps.session_factory() as db:
            resp = retrieval_service.run_search(
                db, deps.embedder, rs, SearchRequest(query=query, top_k=rs.retrieval_default_top_k,
                                                     filters=SearchFilters(topics=topics) if topics else None))
        items = evidence_from_results(resp.results, start=start)
        tool_hits.extend(items)
        return items

    deps.audit.record(
        "llm.request", user_id=state.user_id, session_id=state.session_id, correlation_id=state.correlation_id,
        metadata={"mode": state.mode, "llm": deps.provider.describe(), "evidence_count": len(state.evidence),
                  "patient_fact_count": len(state.facts), "tools_offered": state.mode == "auto",
                  "question_length": len(state.question), "coverage": state.coverage})
    try:
        result = generate_answer(
            deps.provider, deps.llm_settings, mode=state.mode, question=state.question, facts=state.facts,
            evidence=state.evidence, coverage_note=state.coverage_note,
            search=search if state.mode == "auto" else None)
    except LLMError as exc:
        deps.audit.record("llm.response", user_id=state.user_id, session_id=state.session_id,
                          correlation_id=state.correlation_id, severity="error", status="failed",
                          metadata={"error_code": exc.code, "http_status": exc.status})
        return NodeOutcome(updates={"terminal": Terminal(kind="error", code=exc.code, message=str(exc))},
                           status="failed", reason=f"model call failed ({exc.code})", severity="error",
                           details={"error_code": exc.code})
    updates: dict = {"model_answer": result.answer, "evidence": result.evidence, "llm_calls": result.llm_calls,
                     "repair_attempts": result.repair_attempts, "llm_description": deps.provider.describe()}
    if state.mode == "auto":
        searched = bool(result.tool_events and any(t.status == "ok" for t in result.tool_events))
        updates["retrieval"] = RetrievalRecord(
            performed=searched, source="model_tool" if searched else "none",
            settings={"top_k": rs.retrieval_default_top_k}, result_count=len(result.evidence),
            top_similarity=max((e.similarity for e in result.evidence), default=None),
            chunk_ids=[e.chunk_id for e in result.evidence],
            tool_events=[{"name": t.name, "status": t.status, "detail": t.detail,
                          "query_length": len(t.query) if t.query else None, "chunk_ids": t.chunk_ids}
                         for t in result.tool_events])
        updates["coverage"] = "not_applicable" if not searched else "adequate"
    deps.audit.record(
        "llm.response", user_id=state.user_id, session_id=state.session_id, correlation_id=state.correlation_id,
        status="ok", latency_ms=sum(c["latency_ms"] for c in result.llm_calls),
        metadata={"calls": result.llm_calls, "repair_attempts": result.repair_attempts,
                  "tool_events": [{"status": t.status, "detail": t.detail} for t in result.tool_events],
                  "claims_returned": len(result.answer.claims)})
    return NodeOutcome(
        updates=updates, reason=f"model returned {len(result.answer.claims)} claim(s) in {len(result.llm_calls)} call(s)",
        details={"llm_calls": len(result.llm_calls), "repair_attempts": result.repair_attempts,
                 "tool_calls": sum(1 for t in result.tool_events if t.status == "ok"),
                 "claims": len(result.answer.claims)})


# ------------------------------------------------------------------ 7. validate citations
def validate_citations(state: WorkflowState, deps: WorkflowDeps) -> NodeOutcome:
    assert state.model_answer is not None
    retrieval_performed = state.retrieval.performed
    result = validate_answer(state.model_answer, mode=state.mode, evidence=state.evidence, facts=state.facts,
                             retrieval_performed=retrieval_performed)
    body = result.body
    counts = {
        "claims": len(body.claims),
        "evidence_claims": sum(c.basis == "evidence" for c in body.claims),
        "patient_record_claims": sum(c.basis == "patient_record" for c in body.claims),
        "general_knowledge_claims": sum(c.basis == "general_knowledge" for c in body.claims),
        "unsupported_claims": body.unsupported_claim_count,
        "issues": len(result.issues),
        "invalid_citations_removed": sum(i.code == "invalid_citation" for i in result.issues),
        "conflicting_evidence": body.has_conflicting_evidence,
        "cited_chunk_ids": [c.chunk_id for c in body.citations if c.kind == "research_chunk"],
    }
    deps.audit.record("citation.validated", user_id=state.user_id, session_id=state.session_id,
                      correlation_id=state.correlation_id, status="ok", metadata=counts,
                      severity="warning" if result.issues else "info")
    return NodeOutcome(updates={"answer": body, "issues": result.issues},
                       reason=("all citations valid" if not result.issues else f"{len(result.issues)} issue(s) found and repaired"),
                       details=counts, severity="warning" if result.issues else "info")


# ------------------------------------------------------------------ 8. finalize
def finalize(state: WorkflowState, deps: WorkflowDeps) -> NodeOutcome:
    if state.terminal is None:
        return NodeOutcome(updates={"terminal": Terminal(kind="answered")}, reason="answer assembled")
    sev = "error" if state.terminal.kind == "error" else "info"
    return NodeOutcome(reason=f"ended with {state.terminal.kind}", details={"terminal": state.terminal.kind,
                                                                           "outcome_code": state.terminal.code}, severity=sev)
