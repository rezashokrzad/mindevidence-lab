"""Provider-neutral LLM interface.

The application depends only on `LLMProvider`; DeepSeek is one implementation. To use another
model, implement `complete()` and return an `LLMResponse`. Nothing else changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


class LLMError(Exception):
    """Base class. `code` is stable and safe to show; messages never contain keys or prompts."""

    code = "llm_error"
    retryable = False

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMNotConfigured(LLMError):
    code = "llm_not_configured"


class LLMAuthError(LLMError):
    code = "llm_auth_failed"


class LLMBalanceError(LLMError):
    code = "llm_insufficient_balance"


class LLMBadRequest(LLMError):
    code = "llm_bad_request"


class LLMRateLimited(LLMError):
    code = "llm_rate_limited"
    retryable = True

    def __init__(self, message: str, *, status: int | None = 429, retry_after: float | None = None):
        super().__init__(message, status=status)
        self.retry_after = retry_after


class LLMUnavailable(LLMError):
    code = "llm_unavailable"
    retryable = True


class LLMTimeout(LLMError):
    code = "llm_timeout"
    retryable = True


class LLMBadOutput(LLMError):
    """The model answered, but not in the required structure (after the bounded repair attempt)."""

    code = "llm_bad_output"


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[dict] | None = None     # assistant messages that requested tools
    tool_call_id: str | None = None          # tool-result messages

    def as_api(self) -> dict:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class LLMRequest:
    messages: list[Message]
    json_output: bool = False
    tools: list[ToolSpec] = field(default_factory=list)
    tool_choice: Literal["auto", "none"] = "auto"
    max_tokens: int | None = None


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str
    model: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempts: int = 1


@runtime_checkable
class LLMProvider(Protocol):
    name: str            # e.g. "deepseek"
    model: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def describe(self) -> dict:
        """Non-secret configuration for display and audit (model, endpoint host, timeouts, retries)."""
        ...
