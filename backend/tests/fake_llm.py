"""A scripted LLM provider for tests: no network, records every request it receives."""
from __future__ import annotations

import json
from typing import Callable

from app.llm.provider import LLMError, LLMRequest, LLMResponse, ToolCall


def answer_json(claims: list[dict], limitations: list[str] | None = None) -> str:
    return json.dumps({"claims": claims, "limitations": limitations or []})


def claim(text: str, basis: str = "evidence", citations: list[str] | None = None,
          conflicts_with: list[str] | None = None) -> dict:
    return {"text": text, "basis": basis, "citations": citations or [], "conflicts_with": conflicts_with or []}


def reply(content: str | None = None, *, tool_calls: list[ToolCall] | None = None, finish: str | None = None,
          model: str = "scripted-model") -> LLMResponse:
    return LLMResponse(content=content, tool_calls=tool_calls or [], model=model, latency_ms=7,
                       finish_reason=finish or ("tool_calls" if tool_calls else "stop"),
                       prompt_tokens=100, completion_tokens=50)


def tool_call(query: str = "opioid maintenance", call_id: str = "call_1", name: str = "search_research_corpus",
              raw_args: str | None = None) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments_json=raw_args if raw_args is not None else json.dumps({"query": query}))


class ScriptedProvider:
    """`script` items are an LLMResponse, an exception to raise, or a callable(request) -> either.
    When the script runs out, the last item repeats."""

    name = "scripted"
    model = "scripted-model"

    def __init__(self, *script):
        self.script: list = list(script)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        item = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        if callable(item):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        return item

    def describe(self) -> dict:
        return {"provider": "scripted", "model": self.model, "configured": True}

    # helpers for assertions
    @property
    def calls(self) -> int:
        return len(self.requests)

    def prompt_text(self, index: int = 0) -> str:
        return "\n".join(m.content or "" for m in self.requests[index].messages)
