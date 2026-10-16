"""Optional Gmail activity reports.

Principles
* Opt-in only: nothing is requested from Google until the user clicks "enable". If the feature is not configured or the
  user has not consented, the rest of the application is unaffected.
* Minimum permission: `gmail.send` (plus identity scopes used only to confirm the same Google account).
* Minimal content: counts only. No questions, answers, patient ids or histories.
* The refresh token is stored encrypted; withdrawal revokes it at Google and deletes it locally.
* The audit log stays the system of record; email is only a courtesy copy of counts.
* Every attempt, success and failure is audited (without tokens or message bodies).
"""
from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.audit import AuditLog
from app.errors import AppError
from app.integrations.google import GoogleAuthError, GoogleOIDCClient
from app.models import AuditEvent, EmailConsent, Feedback, User
from app.reports.config import GMAIL_FLOW_SCOPES, GMAIL_SEND_SCOPE, ReportSettings
from app.reports.gmail import GmailClient, GmailError, RateLimited, ScopeMissing, TokenRevoked
from app.reports.vault import TokenVault, VaultError
from app.security import Principal

WHAT_IS_SENT = [
    "How many questions you asked and how many were answered, refused, or ended without evidence",
    "How many research searches ran, and how many answers contained claims flagged as unsupported",
    "Median answer time, and how many distinct patient cases you worked with (a number, not which ones)",
    "How many answers you rated helpful or not helpful",
]
WHAT_IS_NOT_SENT = ["Your questions", "Answers or research text", "Patient identifiers, histories, or case details",
                    "Anything from your mailbox: the app can only send, never read mail"]


class ReportError(AppError):
    def __init__(self, status: int, code: str, message: str, *, retry_after: int | None = None):
        super().__init__(message, details=[{"message": f"retry_after_seconds={retry_after}"}] if retry_after else None)
        self.status_code, self.code = status, code


@dataclass
class ReportService:
    settings: ReportSettings
    vault: TokenVault | None
    gmail: GmailClient | None
    google: GoogleOIDCClient            # configured with the Gmail redirect URI
    audit: AuditLog

    # ------------------------------------------------------------------ availability and status
    @property
    def available(self) -> bool:
        return bool(self.vault and self.gmail and self.google.configured)

    def _unavailable_reason(self) -> str | None:
        if not self.google.configured:
            return "Google sign-in credentials are not configured on the server."
        if not self.vault:
            return "Email reports are not enabled on this server (no token encryption key is configured)."
        return None

    def status(self, db: Session, principal: Principal) -> dict:
        c = db.get(EmailConsent, principal.user_id)
        return {
            "available": self.available, "unavailable_reason": self._unavailable_reason(),
            "enabled": bool(c and c.status == "enabled"), "status": c.status if c else "never_enabled",
            "granted_scope": c.granted_scope if c else None, "granted_at": c.granted_at if c else None,
            "last_sent_at": c.last_sent_at if c else None, "last_status": c.last_status if c else None,
            "consecutive_failures": c.consecutive_failures if c else 0,
            "requested_scopes": list(GMAIL_FLOW_SCOPES), "recipient": _mask(principal.email),
            "limits": {"min_interval_seconds": self.settings.report_min_interval_seconds,
                       "max_per_day": self.settings.report_max_per_day, "period_days": self.settings.report_period_days},
            "sent": WHAT_IS_SENT, "not_sent": WHAT_IS_NOT_SENT,
        }

    # ------------------------------------------------------------------ enable (incremental consent)
    def begin_enable(self, principal: Principal) -> tuple[str, dict]:
        if not self.available:
            raise ReportError(503, "email_reports_unavailable", self._unavailable_reason() or "Email reports are unavailable.")
        req = self.google.build_authorization_request(scopes=GMAIL_FLOW_SCOPES, access_type="offline",
                                                      prompt="consent", login_hint=principal.email)
        self.audit.record("email.consent_requested", user_id=principal.user_id, status="ok",
                          metadata={"scopes": list(GMAIL_FLOW_SCOPES)})
        return req.url, {"state": req.state, "nonce": req.nonce, "verifier": req.code_verifier,
                         "user_id": str(principal.user_id)}

    def complete_enable(self, db: Session, principal: Principal | None, tx: dict | None, *, code: str | None,
                        state: str | None, error: str | None) -> str | None:
        """Returns None on success, otherwise a short error code for the frontend."""
        def fail(reason: str) -> str:
            self.audit.record("email.consent_failed", user_id=principal.user_id if principal else None,
                              severity="warning", status="failed", metadata={"reason": reason})
            return reason

        if not self.available:
            return fail("unavailable")
        if principal is None:
            return fail("not_signed_in")
        if error:
            return fail("access_denied" if error == "access_denied" else "google_error")
        import secrets as _secrets
        if (tx is None or not state or not isinstance(tx.get("state"), str)
                or not _secrets.compare_digest(state, tx["state"]) or tx.get("user_id") != str(principal.user_id)):
            return fail("invalid_state")
        if not code:
            return fail("invalid_request")
        try:
            tokens = self.google.exchange_code_tokens(code, tx["verifier"])
            claims = self.google.verify_id_token(tokens.get("id_token", ""), expected_nonce=tx["nonce"])
        except GoogleAuthError as exc:
            return fail(exc.code)
        refresh = tokens.get("refresh_token")
        user = db.get(User, principal.user_id)
        # Never keep a token granted by a different Google account than the one signed in.
        if str(claims.get("sub")) != user.google_sub:
            self._revoke_quietly(refresh or tokens.get("access_token"))
            return fail("account_mismatch")
        if GMAIL_SEND_SCOPE not in str(tokens.get("scope", "")).split():
            self._revoke_quietly(refresh or tokens.get("access_token"))
            return fail("scope_not_granted")
        if not refresh:
            return fail("no_refresh_token")
        blob = self.vault.encrypt(refresh)
        db.execute(text("""
            INSERT INTO auth.email_report_consents (user_id, status, granted_scope, encrypted_refresh_token,
                                                    token_key_id, granted_at, disabled_at, consecutive_failures, last_status)
            VALUES (:u, 'enabled', :scope, :blob, :kid, now(), NULL, 0, 'consent_granted')
            ON CONFLICT (user_id) DO UPDATE SET status = 'enabled', granted_scope = EXCLUDED.granted_scope,
              encrypted_refresh_token = EXCLUDED.encrypted_refresh_token, token_key_id = EXCLUDED.token_key_id,
              granted_at = now(), disabled_at = NULL, consecutive_failures = 0, last_status = 'consent_granted'"""),
            {"u": principal.user_id, "scope": GMAIL_SEND_SCOPE, "blob": blob, "kid": self.vault.key_id})
        db.commit()
        self.audit.record("email.consent_granted", user_id=principal.user_id, status="ok",
                          metadata={"scope": GMAIL_SEND_SCOPE})
        return None

    def _revoke_quietly(self, token: str | None) -> None:
        if token and self.gmail:
            self.gmail.revoke(token)

    # ------------------------------------------------------------------ disable (consent withdrawal)
    def disable(self, db: Session, principal: Principal) -> dict:
        c = db.get(EmailConsent, principal.user_id)
        revoked_at_google = None
        if c and c.encrypted_refresh_token and self.vault and self.gmail:
            try:
                revoked_at_google = self.gmail.revoke(self.vault.decrypt(c.encrypted_refresh_token, c.token_key_id))
            except VaultError:
                revoked_at_google = False
        if c:
            c.status, c.encrypted_refresh_token, c.token_key_id = "disabled", None, None
            c.disabled_at, c.last_status, c.consecutive_failures = datetime.now(timezone.utc), "consent_withdrawn", 0
            db.commit()
        self.audit.record("email.consent_withdrawn", user_id=principal.user_id, status="ok",
                          metadata={"had_consent": bool(c), "revoked_at_google": revoked_at_google})
        note = None
        if revoked_at_google is False:
            note = ("The saved permission was deleted here, but Google could not be reached to revoke it. You can remove "
                    "it at https://myaccount.google.com/permissions")
        return {"enabled": False, "revoked_at_google": revoked_at_google, "note": note}

    # ------------------------------------------------------------------ send
    def send_now(self, db: Session, principal: Principal) -> dict:
        s = self.settings
        c = db.get(EmailConsent, principal.user_id)
        if not self.available:
            raise ReportError(503, "email_reports_unavailable", self._unavailable_reason() or "Email reports are unavailable.")
        if not c or c.status != "enabled" or not c.encrypted_refresh_token:
            raise ReportError(409, "email_reports_not_enabled", "Email reports are not enabled for your account.")

        now = datetime.now(timezone.utc)
        if c.last_attempt_at and (now - c.last_attempt_at).total_seconds() < s.report_min_interval_seconds:
            wait = int(s.report_min_interval_seconds - (now - c.last_attempt_at).total_seconds()) + 1
            raise ReportError(429, "report_rate_limited", f"Please wait {wait} s before requesting another report.",
                              retry_after=wait)
        sent_24h = db.scalar(select(func.count()).where(AuditEvent.user_id == principal.user_id,
                                                        AuditEvent.event_type == "email.report_sent",
                                                        AuditEvent.occurred_at > now - timedelta(days=1)))
        if sent_24h >= s.report_max_per_day:
            raise ReportError(429, "report_daily_limit", f"The limit of {s.report_max_per_day} reports per day was reached.",
                              retry_after=3600)

        c.last_attempt_at = now                        # recorded BEFORE sending so a crash cannot allow a retry storm
        db.commit()
        self.audit.record("email.report_attempted", user_id=principal.user_id, status="ok", metadata={})
        try:
            refresh = self.vault.decrypt(c.encrypted_refresh_token, c.token_key_id)
        except VaultError:
            self._set_status(db, principal.user_id, "error", "key_changed", delete_token=True)
            self._failed(principal, "key_changed")
            raise ReportError(409, "consent_invalid", "The saved permission can no longer be used. Enable email reports again.") from None

        summary = build_summary(db, principal.user_id, since=now - timedelta(days=s.report_period_days),
                                period_days=s.report_period_days)
        try:
            access = self.gmail.access_token(refresh)
            self.gmail.send(access, to=principal.email, subject="MindEvidence Lab: your activity summary",
                            body=render_summary(summary))
        except (TokenRevoked, ScopeMissing) as exc:
            self._set_status(db, principal.user_id, "revoked", exc.code, delete_token=True)
            self._failed(principal, exc.code)
            raise ReportError(409, "consent_revoked", str(exc)) from None
        except RateLimited as exc:
            self._bump_failure(db, principal.user_id, "rate_limited")
            self._failed(principal, "rate_limited")
            raise ReportError(429, "gmail_rate_limited", str(exc), retry_after=int(exc.retry_after or 600)) from None
        except GmailError as exc:
            self._bump_failure(db, principal.user_id, exc.code)
            self._failed(principal, exc.code)
            raise ReportError(502, "email_delivery_failed", "The summary could not be delivered. Try again later.") from None

        db.execute(update(EmailConsent).where(EmailConsent.user_id == principal.user_id)
                   .values(last_sent_at=now, last_status="sent", consecutive_failures=0))
        db.commit()
        self.audit.record("email.report_sent", user_id=principal.user_id, status="ok", metadata={"summary": summary})
        return {"sent": True, "recipient": _mask(principal.email), "summary": summary}

    def _failed(self, principal: Principal, reason: str) -> None:
        self.audit.record("email.report_failed", user_id=principal.user_id, severity="error", status="failed",
                          metadata={"reason": reason})

    def _set_status(self, db: Session, user_id: uuid.UUID, status: str, last: str, *, delete_token: bool) -> None:
        vals = {"status": status, "last_status": last}
        if delete_token:
            vals.update(encrypted_refresh_token=None, token_key_id=None)
        db.execute(update(EmailConsent).where(EmailConsent.user_id == user_id).values(**vals))
        db.commit()
        if status == "revoked":
            self.audit.record("email.consent_revoked", user_id=user_id, severity="warning", status="ok",
                              metadata={"reason": last})

    def _bump_failure(self, db: Session, user_id: uuid.UUID, last: str) -> None:
        db.execute(update(EmailConsent).where(EmailConsent.user_id == user_id)
                   .values(last_status=last, consecutive_failures=EmailConsent.consecutive_failures + 1))
        db.commit()


# ---------------------------------------------------------------------- summary (counts only)

def build_summary(db: Session, user_id: uuid.UUID, *, since: datetime, period_days: int) -> dict:
    ev = db.execute(select(AuditEvent.event_type, AuditEvent.patient_id, AuditEvent.latency_ms, AuditEvent.event_metadata)
                    .where(AuditEvent.user_id == user_id, AuditEvent.occurred_at >= since)).all()
    submitted = [e for e in ev if e.event_type == "question.submitted"]
    completed = [e for e in ev if e.event_type == "answer.completed"]
    outcomes: dict[str, int] = {}
    for e in completed:
        k = e.event_metadata.get("outcome") or "unknown"
        outcomes[k] = outcomes.get(k, 0) + 1
    lat = [e.latency_ms for e in completed if e.latency_ms is not None]
    rating = dict(db.execute(select(Feedback.rating, func.count()).where(Feedback.user_id == user_id,
                                                                         Feedback.created_at >= since)
                             .group_by(Feedback.rating)).all())
    return {
        "period_days": period_days,
        "questions_asked": len(submitted),
        "answers": {"answered": outcomes.get("answered", 0), "refused": outcomes.get("refused", 0),
                    "unsupported": outcomes.get("unsupported", 0), "no_evidence": outcomes.get("no_evidence", 0),
                    "urgent_escalations": outcomes.get("urgent", 0), "errors": outcomes.get("error", 0)},
        "research_searches": sum(1 for e in ev if e.event_type == "retrieval.executed"),
        "answers_with_unsupported_claims": sum(1 for e in completed if (e.event_metadata.get("unsupported_claims") or 0) > 0),
        "median_answer_seconds": round(statistics.median(lat) / 1000, 1) if lat else None,
        "distinct_cases_worked_with": len({e.patient_id for e in submitted if e.patient_id}),
        "feedback": {"helpful": rating.get("helpful", 0), "not_helpful": rating.get("not_helpful", 0)},
    }


def render_summary(s: dict) -> str:
    a = s["answers"]
    lines = [
        f"MindEvidence Lab: activity summary for the last {s['period_days']} days", "",
        f"Questions asked: {s['questions_asked']}",
        f"  answered: {a['answered']} | refused (outside scope of this tool): {a['refused']} | "
        f"unsupported questions: {a['unsupported']} | ended without evidence: {a['no_evidence']} | "
        f"urgent-wording escalations: {a['urgent_escalations']} | errors: {a['errors']}",
        f"Research searches run: {s['research_searches']}",
        f"Answers containing claims flagged as unsupported: {s['answers_with_unsupported_claims']}",
        f"Median answer time: {s['median_answer_seconds'] if s['median_answer_seconds'] is not None else 'n/a'} seconds",
        f"Distinct patient cases worked with: {s['distinct_cases_worked_with']}",
        f"Answers rated helpful: {s['feedback']['helpful']} | not helpful: {s['feedback']['not_helpful']}", "",
        "This message contains counts only. It does not include your questions, answers, research text, or any patient",
        "information. MindEvidence Lab is an educational tool and does not give clinical advice.",
        "You can turn email summaries off at any time in the app (this also revokes the permission at Google).",
    ]
    return "\n".join(lines)


def _mask(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'*' * max(1, len(local) - 1)}@{domain}"
