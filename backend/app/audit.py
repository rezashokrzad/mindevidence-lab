"""Audit logging service.

`AuditLog.record()` writes one append-only row to audit.events in its own short transaction, so an audit
write never rides on (or rolls back with) a request transaction. A failure to write an audit event is
logged as an error and does not break the user's request; it is never silent.

Privacy: `sanitize()` is applied to every metadata dict. It
* drops keys that look like secrets (token, secret, password, api key, authorization, cookie, code_verifier, ...),
* drops keys that carry free text we deliberately never persist (question, answer, prompt, comment, text, ...),
* truncates long strings and caps list/dict sizes,
so audit events hold ids, counts, lengths, hashes, settings, and statuses only.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditEvent

log = logging.getLogger(__name__)

_SECRET_KEY = re.compile(r"token|secret|password|passwd|api[_-]?key|apikey|authorization|cookie|verifier|bearer|credential|"
                         r"^code$|csrf|session_token", re.I)
_FREE_TEXT_KEY = re.compile(r"^(question|answer|prompt|completion|content|comment|text|message_text|body|query_text|"
                            r"reasoning|reasoning_content|chunk_text|passage)$", re.I)
MAX_STR = 300
MAX_ITEMS = 50
MAX_DEPTH = 4


def sanitize(value: Any, _depth: int = 0) -> Any:
    if _depth > MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:MAX_ITEMS]:
            key = str(k)[:60]
            if _SECRET_KEY.search(key) or _FREE_TEXT_KEY.match(key):
                continue
            out[key] = sanitize(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [sanitize(v, _depth + 1) for v in list(value)[:MAX_ITEMS]]
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else value[: MAX_STR - 1] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)[:MAX_STR]


class AuditLog:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._factory = session_factory

    def record(self, event_type: str, *, user_id: uuid.UUID | None = None, session_id: uuid.UUID | None = None,
               correlation_id: str | None = None, severity: str = "info", node: str | None = None,
               status: str | None = None, latency_ms: int | None = None, patient_id: str | None = None,
               metadata: dict | None = None) -> bool:
        """Write one event. Returns False (and logs an error) if it could not be written."""
        try:
            with self._factory() as db:
                db.execute(insert(AuditEvent).values(
                    event_type=event_type, user_id=user_id, session_id=session_id, correlation_id=correlation_id,
                    severity=severity, node=node, status=status,
                    latency_ms=None if latency_ms is None else max(0, int(latency_ms)),
                    patient_id=patient_id, event_metadata=sanitize(metadata or {})))
                db.commit()
            return True
        except Exception:
            log.exception("AUDIT WRITE FAILED for event %s (correlation %s)", event_type, correlation_id)
            return False
