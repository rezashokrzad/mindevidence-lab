"""Audit use cases: browse events (own, or all for instructors), record allowed UI events, build the dashboard."""
from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditLog
from app.errors import UnknownPatientReference
from app.models import AuditEvent, LoginSession
from app.repositories import audit as repo
from app.repositories import patients as patient_repo
from app.schemas.audit import (
    ActivityResponse,
    ActivityTotals,
    AnswerActivity,
    AuditEventOut,
    AuditEventPage,
    NodeSummary,
    UiEventOut,
    UiEventRequest,
)
from app.security import Principal


def to_out(e: AuditEvent) -> AuditEventOut:
    return AuditEventOut(event_id=e.event_id, event_seq=e.event_seq, occurred_at=e.occurred_at, user_id=e.user_id,
                         session_id=e.session_id, correlation_id=e.correlation_id, event_type=e.event_type,
                         severity=e.severity, node=e.node, status=e.status, latency_ms=e.latency_ms,
                         patient_id=e.patient_id, metadata=e.event_metadata)


def list_events(db: Session, principal: Principal, *, scope: str, correlation_id: str | None,
                session_id: uuid.UUID | None, event_type: str | None, min_severity: str | None,
                limit: int, offset: int) -> AuditEventPage:
    """Students see only their own events. Instructors may ask for scope=all."""
    effective = "all" if (scope == "all" and principal.role == "instructor") else "own"
    rows, total = repo.list_events(db, user_id=None if effective == "all" else principal.user_id,
                                   correlation_id=correlation_id, session_id=session_id, event_type=event_type,
                                   min_severity=min_severity, limit=limit, offset=offset)
    return AuditEventPage(items=[to_out(r) for r in rows], total=total, limit=limit, offset=offset, scope=effective)


def record_ui_event(db: Session, audit: AuditLog, principal: Principal, body: UiEventRequest) -> UiEventOut:
    if body.event_type == "patient.selected" and not patient_repo.patient_exists(db, body.patient_id):
        raise UnknownPatientReference(body.patient_id)
    ok = audit.record(body.event_type, user_id=principal.user_id, correlation_id=body.correlation_id,
                      patient_id=body.patient_id, status="ok",
                      metadata={"mode": body.mode, "chunk_id": body.chunk_id, "source": "ui"})
    return UiEventOut(recorded=ok)


def activity(db: Session, principal: Principal, *, limit_events: int = 200) -> ActivityResponse:
    """The signed-in user's activity since the CURRENT login session started (their own events only)."""
    login = db.get(LoginSession, principal.login_session_id)
    since = login.created_at
    rows = list(db.scalars(select(AuditEvent).where(AuditEvent.user_id == principal.user_id,
                                                    AuditEvent.occurred_at >= since)
                           .order_by(AuditEvent.event_seq)).all())
    by_cid: dict[str, list[AuditEvent]] = defaultdict(list)
    for e in rows:
        if e.correlation_id:
            by_cid[e.correlation_id].append(e)

    answers: list[AnswerActivity] = []
    model_config_seen = None
    for cid, evs in by_cid.items():
        if not any(e.event_type == "question.submitted" for e in evs):
            continue
        first = evs[0]
        completed = next((e for e in reversed(evs) if e.event_type == "answer.completed"), None)
        retrieval = next((e for e in evs if e.event_type == "retrieval.executed"), None)
        llm_req = next((e for e in evs if e.event_type == "llm.request"), None)
        llm_resp = next((e for e in evs if e.event_type == "llm.response"), None)
        nodes = [NodeSummary(node=e.node, status=e.status, latency_ms=e.latency_ms,
                             reason=e.event_metadata.get("reason")) for e in evs if e.event_type == "workflow.node"]
        meta = completed.event_metadata if completed else {}
        if llm_req:
            model_config_seen = llm_req.event_metadata.get("llm") or model_config_seen
        tool = next((e for e in evs if e.event_type == "workflow.node" and e.node == "generate"), None)
        fb = next((e.event_metadata.get("rating") for e in reversed(evs) if e.event_type == "feedback.submitted"), None)
        answers.append(AnswerActivity(
            correlation_id=cid, started_at=first.occurred_at, mode=first.event_metadata.get("mode"),
            outcome=meta.get("outcome"), outcome_code=meta.get("outcome_code"), question_class=meta.get("question_class"),
            patient_id=next((e.patient_id for e in evs if e.patient_id), None),
            latency_ms=completed.latency_ms if completed else None, total_node_ms=sum(n.latency_ms or 0 for n in nodes),
            claims=meta.get("claims"), unsupported_claims=meta.get("unsupported_claims"),
            retrieval=({**retrieval.event_metadata, "status": retrieval.status} if retrieval else
                       ({"performed": False, "coverage": meta.get("coverage"),
                         "model_tool": (tool.event_metadata.get("tool_calls") if tool else None)}
                        if completed else None)),
            model=({"request": {k: v for k, v in (llm_req.event_metadata if llm_req else {}).items() if k != "llm"},
                    "response": llm_resp.event_metadata if llm_resp else None, "status": llm_resp.status if llm_resp else None}
                   if llm_req else None),
            nodes=nodes, errors=[f"{e.event_type}: {e.event_metadata.get('error_code') or e.event_metadata.get('reason') or e.status}"
                                 for e in evs if e.severity == "error"],
            feedback=fb))
    answers.sort(key=lambda a: a.started_at, reverse=True)

    lat = [a.latency_ms for a in answers if a.latency_ms is not None]
    totals = ActivityTotals(
        answers=len(answers), answered=sum(a.outcome == "answered" for a in answers),
        refused_or_unsupported=sum(a.outcome in ("refused", "unsupported", "urgent", "no_evidence") for a in answers),
        errors=sum(1 for e in rows if e.severity == "error"),
        retrievals=sum(1 for e in rows if e.event_type == "retrieval.executed"),
        unsupported_claims=sum(a.unsupported_claims or 0 for a in answers),
        median_latency_ms=int(statistics.median(lat)) if lat else None, max_latency_ms=max(lat) if lat else None)
    shown = rows[-limit_events:]
    return ActivityResponse(
        since=since, login_session_started_at=since, totals=totals, model_config_in_use=model_config_seen,
        answers=answers, events=[to_out(e) for e in reversed(shown)],
        errors=[to_out(e) for e in reversed(rows) if e.severity == "error"][:50])
