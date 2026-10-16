"""Call the LLM and obtain a validated `ModelAnswer`.

* Output must be one JSON object matching `ModelAnswer`; one bounded repair attempt is made if it is not.
* Tool availability is decided HERE (code), not by the model: tools are offered only in `auto` mode, only the
  `search_research_corpus` tool exists, arguments are validated, and the number of calls is capped. After the cap
  the model is called with tool_choice="none".
* The model's reasoning content (if any) is never read.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.answering import prompts
from app.answering.schemas import EvidenceItem, Mode, ModelAnswer, PatientFact
from app.llm.config import LLMSettings
from app.llm.provider import LLMBadOutput, LLMProvider, LLMRequest, LLMResponse, Message, ToolSpec

log = logging.getLogger(__name__)

SEARCH_TOOL = ToolSpec("search_research_corpus", prompts.SEARCH_TOOL_DESCRIPTION, prompts.SEARCH_TOOL_PARAMETERS)


class SearchToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
    topics: list[str] | None = Field(default=None, max_length=5)


@dataclass
class ToolEvent:
    name: str
    status: str                 # ok | rejected | limit_reached
    detail: str
    query: str | None = None
    chunk_ids: list[str] = field(default_factory=list)


@dataclass
class GenerationResult:
    answer: ModelAnswer
    evidence: list[EvidenceItem]
    llm_calls: list[dict]               # metadata only: model, latency, tokens, finish_reason, attempts
    tool_events: list[ToolEvent]
    repair_attempts: int


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_model_json(content: str | None) -> dict:
    if not content or not content.strip():
        raise ValueError("empty reply")
    text = _FENCE.sub("", content.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON ({exc.msg})") from None
    if not isinstance(data, dict):
        raise ValueError("JSON is not an object")
    return data


def _meta(resp: LLMResponse) -> dict:
    return {"model": resp.model, "latency_ms": resp.latency_ms, "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens, "finish_reason": resp.finish_reason, "attempts": resp.attempts,
            "tool_calls_requested": [c.name for c in resp.tool_calls]}


def generate_answer(
    provider: LLMProvider, settings: LLMSettings, *, mode: Mode, question: str, facts: list[PatientFact],
    evidence: list[EvidenceItem], coverage_note: str | None = None,
    search: Callable[[str, list[str] | None, int], list[EvidenceItem]] | None = None,
) -> GenerationResult:
    """`search(query, topics, start_index)` runs retrieval for the auto-mode tool and returns new EvidenceItems."""
    evidence = list(evidence)
    messages = [Message("system", prompts.system_prompt(mode)),
                Message("user", prompts.user_prompt(question, facts, evidence, mode, coverage_note))]
    tools_allowed = mode == "auto" and search is not None and settings.llm_max_tool_calls > 0
    llm_calls: list[dict] = []
    tool_events: list[ToolEvent] = []
    tool_calls_used, repairs = 0, 0

    for _ in range(settings.llm_max_tool_calls + settings.llm_max_repair_attempts + 2):
        offer_tools = tools_allowed and tool_calls_used < settings.llm_max_tool_calls
        resp = provider.complete(LLMRequest(
            messages=messages, json_output=not offer_tools, tools=[SEARCH_TOOL] if offer_tools else [],
            tool_choice="auto" if offer_tools else "none"))
        llm_calls.append(_meta(resp))

        if resp.tool_calls:
            if not offer_tools:                    # the model asked for a tool it was not offered: ignore it
                tool_events.append(ToolEvent("(unavailable)", "rejected", "tool call ignored: tools are not available"))
                messages.append(Message("assistant", resp.content or ""))
                messages.append(Message("user", prompts.repair_prompt("tools are not available")))
                continue
            messages.append(Message("assistant", resp.content,
                                    tool_calls=[{"id": c.id, "type": "function",
                                                 "function": {"name": c.name, "arguments": c.arguments_json}}
                                                for c in resp.tool_calls]))
            for call in resp.tool_calls:
                content = _run_tool(call, search, evidence, settings, tool_calls_used, tool_events)
                tool_calls_used += call.name == "search_research_corpus"
                messages.append(Message("tool", content, tool_call_id=call.id))
            continue

        try:
            if resp.finish_reason == "length":
                raise ValueError("reply was cut off (token limit)")
            answer = ModelAnswer.model_validate(parse_model_json(resp.content))
            return GenerationResult(answer, evidence, llm_calls, tool_events, repairs)
        except (ValueError, ValidationError) as exc:
            reason = str(exc).splitlines()[0][:160]
            if repairs >= settings.llm_max_repair_attempts:
                raise LLMBadOutput(f"The language model did not return the required answer structure ({reason}).") from None
            repairs += 1
            log.info("model output rejected (%s); repair attempt %d", reason, repairs)
            messages.append(Message("assistant", resp.content or ""))
            messages.append(Message("user", prompts.repair_prompt(reason)))
    raise LLMBadOutput("The language model did not produce a usable answer within the call limit.")


def _run_tool(call, search, evidence: list[EvidenceItem], settings: LLMSettings, used: int,
              events: list[ToolEvent]) -> str:
    if call.name != "search_research_corpus":
        events.append(ToolEvent(call.name, "rejected", "unknown tool"))
        return "Error: unknown tool. Only search_research_corpus exists."
    if used >= settings.llm_max_tool_calls:
        events.append(ToolEvent(call.name, "limit_reached", f"limit of {settings.llm_max_tool_calls} tool calls"))
        return "Error: the search limit was reached. Answer with what you have."
    try:
        args = SearchToolArgs.model_validate(json.loads(call.arguments_json or "{}"))
        allowed = {"alcohol", "cannabis", "opioids", "stimulants", "behavioral"}
        topics = [t for t in (args.topics or []) if t in allowed] or None
    except (ValueError, ValidationError) as exc:
        events.append(ToolEvent(call.name, "rejected", f"invalid arguments: {str(exc).splitlines()[0][:120]}"))
        return "Error: invalid arguments. Provide a query of 3 to 500 characters."
    found = search(args.query, topics, len(evidence) + 1)
    # The backend owns citation ids: a chunk already shown keeps its id; new chunks get the next free id.
    known = {e.chunk_id: e for e in evidence}
    shown: list[EvidenceItem] = []
    for item in found:
        if item.chunk_id in known:
            shown.append(known[item.chunk_id])
        else:
            item = item.model_copy(update={"citation_id": f"E{len(evidence) + 1}"})
            evidence.append(item)
            known[item.chunk_id] = item
            shown.append(item)
    events.append(ToolEvent(call.name, "ok", f"{len(found)} passages returned", args.query, [e.chunk_id for e in shown]))
    return prompts.tool_result_text(shown)
