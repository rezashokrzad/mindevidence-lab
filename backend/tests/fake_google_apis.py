"""Offline stand-in for Google's token, revoke, and Gmail send endpoints. Records every call."""
from __future__ import annotations

import base64
import email
import json
from urllib.parse import parse_qs

import httpx

from app.reports.config import ReportSettings

REFRESH_TOKEN = "1//refresh-token-NOT-REAL-0123456789"
ACCESS_TOKEN = "ya29.access-token-NOT-REAL"


class FakeGoogleApis:
    def __init__(self):
        self.token_calls: list[dict] = []
        self.revoke_calls: list[dict] = []
        self.send_calls: list[dict] = []
        # each is a callable() -> httpx.Response, or a list of them consumed in order (last repeats)
        self.token_behavior = [lambda: httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3599})]
        self.send_behavior = [lambda: httpx.Response(200, json={"id": "msg-1", "labelIds": ["SENT"]})]
        self.revoke_behavior = [lambda: httpx.Response(200, json={})]

    @staticmethod
    def _next(behaviors: list, n: int):
        return behaviors[min(n, len(behaviors) - 1)]()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_calls.append(form)
            return self._next(self.token_behavior, len(self.token_calls) - 1)
        if url.endswith("/revoke"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.revoke_calls.append(form)
            return self._next(self.revoke_behavior, len(self.revoke_calls) - 1)
        if url.endswith("/gmail/v1/users/me/messages/send"):
            body = json.loads(request.content)
            msg = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
            self.send_calls.append({"authorization": request.headers.get("authorization"), "to": msg["To"],
                                    "subject": msg["Subject"], "body": msg.get_payload(decode=True).decode(),
                                    "url": url, "keys": sorted(body)})
            return self._next(self.send_behavior, len(self.send_calls) - 1)
        return httpx.Response(404, json={"error": "unexpected url " + url})

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def error(status: int, body: dict | None = None, headers: dict | None = None):
    return lambda: httpx.Response(status, json=body or {"error": {"message": "error"}}, headers=headers)


def settings(**kw) -> ReportSettings:
    from cryptography.fernet import Fernet

    values = {"email_token_encryption_key": Fernet.generate_key().decode(), "report_min_interval_seconds": 0,
              "report_retry_backoff_seconds": 0}
    values.update(kw)
    return ReportSettings(**values)
