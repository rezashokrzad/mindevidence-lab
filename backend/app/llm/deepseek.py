"""DeepSeek chat-completions client (backend only).

Follows DeepSeek's API reference (https://api-docs.deepseek.com): POST {base}/chat/completions with
`Authorization: Bearer <key>`, OpenAI-compatible messages/tools, `response_format: {"type": "json_object"}`
(the prompt must also ask for JSON), `thinking: {"type": ...}`.

* The API key is read from the environment, sent only in the Authorization header, and never logged.
* Prompts and completions are not logged here either (only status, latency, token counts).
* Retries are bounded: 429, 500, 503 and timeouts (exponential backoff, honouring Retry-After).
  400/401/402/422 are never retried.
* `reasoning_content` (the model's reasoning mode output) is ignored on purpose.
"""
from __future__ import annotations

import logging
import time

import httpx

from app.llm.config import LLMSettings
from app.llm.provider import (
    LLMAuthError,
    LLMBadOutput,
    LLMBadRequest,
    LLMBalanceError,
    LLMError,
    LLMNotConfigured,
    LLMRateLimited,
    LLMRequest,
    LLMResponse,
    LLMTimeout,
    LLMUnavailable,
    ToolCall,
)

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 503}


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, settings: LLMSettings, *, http_client: httpx.Client | None = None, sleep=time.sleep):
        self._s = settings
        self.model = settings.deepseek_model
        self._sleep = sleep
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(
            settings.deepseek_read_timeout_seconds, connect=settings.deepseek_connect_timeout_seconds))

    # ---- request building ----
    def _payload(self, req: LLMRequest) -> dict:
        s = self._s
        body: dict = {
            "model": s.deepseek_model,
            "messages": [m.as_api() for m in req.messages],
            "thinking": {"type": s.deepseek_thinking},
            "max_tokens": req.max_tokens or s.deepseek_max_output_tokens,
            "stream": False,
        }
        if s.deepseek_thinking == "disabled":
            body["temperature"] = s.deepseek_temperature          # has no effect in thinking mode
        if req.json_output:
            body["response_format"] = {"type": "json_object"}
        if req.tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                "parameters": t.parameters}} for t in req.tools]
            body["tool_choice"] = req.tool_choice
        return body

    # ---- public API ----
    def complete(self, request: LLMRequest) -> LLMResponse:
        key = self._s.deepseek_api_key.get_secret_value()
        if not key:
            raise LLMNotConfigured("DEEPSEEK_API_KEY is not set on the server.")
        url = f"{self._s.deepseek_base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = self._payload(request)

        last: LLMError | None = None
        started = time.monotonic()
        for attempt in range(1, self._s.deepseek_max_retries + 2):
            try:
                resp = self._http.post(url, json=payload, headers=headers)
            except httpx.TimeoutException:
                last = LLMTimeout("The language model did not respond in time.")
            except httpx.HTTPError as exc:
                last = LLMUnavailable(f"Could not reach the language model service ({type(exc).__name__}).")
            else:
                try:
                    return self._parse(resp, started, attempt)
                except LLMError as exc:
                    if not exc.retryable:
                        raise
                    last = exc
            if attempt > self._s.deepseek_max_retries:
                break
            delay = self._s.deepseek_retry_backoff_seconds * 2 ** (attempt - 1)
            if isinstance(last, LLMRateLimited) and last.retry_after is not None:
                delay = max(delay, min(last.retry_after, 30.0))
            log.warning("LLM call failed (%s, attempt %d); retrying in %.1fs", last.code, attempt, delay)
            self._sleep(delay)
        assert last is not None
        raise last

    def describe(self) -> dict:
        s = self._s
        return {
            "provider": "deepseek", "model": s.deepseek_model, "endpoint_host": httpx.URL(s.deepseek_base_url).host,
            "thinking": s.deepseek_thinking, "temperature": s.deepseek_temperature,
            "max_output_tokens": s.deepseek_max_output_tokens,
            "timeouts_seconds": {"connect": s.deepseek_connect_timeout_seconds, "read": s.deepseek_read_timeout_seconds},
            "retry_policy": {"max_retries": s.deepseek_max_retries, "backoff_seconds": s.deepseek_retry_backoff_seconds,
                             "retry_on": sorted(RETRYABLE_STATUS) + ["timeout"]},
            "max_tool_calls": s.llm_max_tool_calls, "configured": s.configured,
        }

    # ---- response handling ----
    def _parse(self, resp: httpx.Response, started: float, attempts: int) -> LLMResponse:
        status = resp.status_code
        if status == 200:
            try:
                data = resp.json()
                choice = data["choices"][0]
                msg = choice["message"]
            except (ValueError, KeyError, IndexError, TypeError):
                raise LLMBadOutput("The language model returned an unreadable response.") from None
            calls = [ToolCall(id=str(c.get("id", "")), name=str(c["function"]["name"]),
                              arguments_json=str(c["function"].get("arguments") or "{}"))
                     for c in (msg.get("tool_calls") or []) if c.get("type", "function") == "function"]
            usage = data.get("usage") or {}
            return LLMResponse(
                content=msg.get("content"), tool_calls=calls, finish_reason=str(choice.get("finish_reason") or ""),
                model=str(data.get("model") or self.model), latency_ms=int((time.monotonic() - started) * 1000),
                prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                attempts=attempts)
        # error bodies may echo request details: keep only the status in our message
        if status == 401:
            raise LLMAuthError("The language model service rejected the API key.", status=status)
        if status == 402:
            raise LLMBalanceError("The language model account has insufficient balance.", status=status)
        if status in (400, 422):
            raise LLMBadRequest(f"The language model rejected the request (HTTP {status}).", status=status)
        if status == 429:
            ra = resp.headers.get("retry-after")
            raise LLMRateLimited("The language model service is rate limiting requests.", status=status,
                                 retry_after=float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
        if status in (500, 503):
            raise LLMUnavailable(f"The language model service is unavailable (HTTP {status}).", status=status)
        raise LLMUnavailable(f"Unexpected response from the language model service (HTTP {status}).", status=status)
