"""DeepSeek client against a mock transport (no network). Checks the documented wire format, bounded retries,
error mapping, and that the API key never appears in logs or error messages."""
import json
import logging

import httpx
import pytest
from pydantic import ValidationError

from app.llm.config import LLMSettings
from app.llm.deepseek import DeepSeekProvider
from app.llm.provider import (
    LLMAuthError,
    LLMBadOutput,
    LLMBadRequest,
    LLMBalanceError,
    LLMNotConfigured,
    LLMProvider,
    LLMRateLimited,
    LLMRequest,
    LLMTimeout,
    LLMUnavailable,
    Message,
    ToolSpec,
)

KEY = "sk-test-NOT-A-REAL-KEY-0123456789"


def ok_body(content='{"claims": []}', **extra):
    return {"id": "x", "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": content, **extra}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


class Server:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def provider(server, **overrides):
    s = LLMSettings(deepseek_api_key=KEY, deepseek_retry_backoff_seconds=1.0, **overrides)
    sleeps = []
    p = DeepSeekProvider(s, http_client=httpx.Client(transport=httpx.MockTransport(server)), sleep=sleeps.append)
    p.sleeps = sleeps
    return p


def req(**kw):
    return LLMRequest(messages=[Message("system", "be careful"), Message("user", "hello")], **kw)


def resp(status, body=None, headers=None):
    return httpx.Response(status, json=body if body is not None else {"error": {"message": "details with the prompt"}},
                          headers=headers)


# ---------- wire format ----------

def test_request_matches_the_documented_format():
    srv = Server(httpx.Response(200, json=ok_body()))
    p = provider(srv)
    r = p.complete(req(json_output=True))
    sent = srv.requests[0]
    assert str(sent.url) == "https://api.deepseek.com/chat/completions" and sent.method == "POST"
    assert sent.headers["authorization"] == f"Bearer {KEY}" and sent.headers["content-type"] == "application/json"
    body = json.loads(sent.content)
    assert body["model"] == "deepseek-flash" and body["stream"] is False
    assert body["thinking"] == {"type": "disabled"} and body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [{"role": "system", "content": "be careful"}, {"role": "user", "content": "hello"}]
    assert body["max_tokens"] == 1800 and body["temperature"] == 0.2 and "tools" not in body
    assert r.content == '{"claims": []}' and r.prompt_tokens == 11 and r.completion_tokens == 7 and r.model == "deepseek-flash"
    assert isinstance(p, LLMProvider)


def test_tools_and_thinking_settings_are_sent_as_documented():
    srv = Server(httpx.Response(200, json=ok_body()))
    provider(srv, deepseek_thinking="enabled").complete(req(tools=[ToolSpec("search", "desc", {"type": "object"})],
                                                            tool_choice="auto"))
    body = json.loads(srv.requests[0].content)
    assert body["thinking"] == {"type": "enabled"} and "temperature" not in body        # no effect in thinking mode
    assert body["tools"] == [{"type": "function", "function": {"name": "search", "description": "desc",
                                                                "parameters": {"type": "object"}}}]
    assert body["tool_choice"] == "auto"


def test_custom_model_and_endpoint_from_settings():
    srv = Server(httpx.Response(200, json=ok_body()))
    provider(srv, deepseek_model="deepseek-v4-pro", deepseek_base_url="https://llm.example.org/v1/").complete(req())
    assert str(srv.requests[0].url) == "https://llm.example.org/v1/chat/completions"
    assert json.loads(srv.requests[0].content)["model"] == "deepseek-v4-pro"


def test_tool_calls_are_parsed_and_reasoning_content_is_ignored():
    body = ok_body(content=None, reasoning_content="PRIVATE CHAIN OF THOUGHT",
                   tool_calls=[{"id": "c1", "type": "function", "function": {"name": "search", "arguments": '{"query": "x"}'}}])
    body["choices"][0]["finish_reason"] = "tool_calls"
    r = provider(Server(httpx.Response(200, json=body))).complete(req())
    assert r.tool_calls[0].name == "search" and r.tool_calls[0].arguments_json == '{"query": "x"}' and r.finish_reason == "tool_calls"
    assert "CHAIN OF THOUGHT" not in repr(r)


# ---------- retries ----------

@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_statuses_are_retried_then_succeed(status):
    srv = Server(resp(status), resp(status), httpx.Response(200, json=ok_body()))
    p = provider(srv)
    assert p.complete(req()).attempts == 3 and len(srv.requests) == 3 and p.sleeps == [1.0, 2.0]


def test_retries_are_bounded_and_the_last_error_is_raised():
    srv = Server(resp(503))
    p = provider(srv, deepseek_max_retries=2)
    with pytest.raises(LLMUnavailable, match="HTTP 503"):
        p.complete(req())
    assert len(srv.requests) == 3


def test_retry_after_is_honoured_for_rate_limits():
    srv = Server(resp(429, headers={"retry-after": "7"}), httpx.Response(200, json=ok_body()))
    p = provider(srv)
    p.complete(req())
    assert p.sleeps == [7.0]


def test_rate_limit_error_after_retries():
    with pytest.raises(LLMRateLimited):
        provider(Server(resp(429)), deepseek_max_retries=1).complete(req())


@pytest.mark.parametrize("exc, error", [(httpx.ReadTimeout("slow"), LLMTimeout), (httpx.ConnectError("down"), LLMUnavailable)])
def test_network_failures_are_retried_and_mapped(exc, error):
    srv = Server(exc, httpx.Response(200, json=ok_body()))
    assert provider(srv).complete(req()).attempts == 2
    with pytest.raises(error):
        provider(Server(exc), deepseek_max_retries=1).complete(req())


@pytest.mark.parametrize("status, error", [(401, LLMAuthError), (402, LLMBalanceError), (400, LLMBadRequest), (422, LLMBadRequest)])
def test_permanent_errors_are_not_retried(status, error):
    srv = Server(resp(status))
    with pytest.raises(error):
        provider(srv).complete(req())
    assert len(srv.requests) == 1


def test_unreadable_success_response_is_bad_output():
    with pytest.raises(LLMBadOutput):
        provider(Server(httpx.Response(200, text="<html>nope</html>"))).complete(req())
    with pytest.raises(LLMBadOutput):
        provider(Server(httpx.Response(200, json={"choices": []}))).complete(req())


# ---------- key handling ----------

def test_missing_key_is_a_clear_error_without_any_request():
    srv = Server(httpx.Response(200, json=ok_body()))
    p = DeepSeekProvider(LLMSettings(deepseek_api_key=""), http_client=httpx.Client(transport=httpx.MockTransport(srv)))
    with pytest.raises(LLMNotConfigured, match="DEEPSEEK_API_KEY"):
        p.complete(req())
    assert srv.requests == []


def test_key_and_prompts_never_reach_logs_or_error_messages(caplog):
    caplog.set_level(logging.DEBUG)
    p = provider(Server(resp(500), resp(401)))
    with pytest.raises(LLMAuthError) as exc:
        p.complete(LLMRequest(messages=[Message("user", "SECRET STUDENT QUESTION")]))
    assert KEY not in str(exc.value) and "SECRET STUDENT QUESTION" not in str(exc.value)
    assert KEY not in caplog.text and "SECRET STUDENT QUESTION" not in caplog.text and "details with the prompt" not in caplog.text


def test_describe_exposes_no_secret():
    d = provider(Server()).describe()
    assert KEY not in json.dumps(d) and d["model"] == "deepseek-flash" and d["endpoint_host"] == "api.deepseek.com"
    assert d["retry_policy"]["retry_on"] == [429, 500, 503, "timeout"] and d["configured"] is True


def test_settings_hide_the_key_and_require_https():
    s = LLMSettings(deepseek_api_key=KEY)
    assert KEY not in repr(s) and KEY not in str(s.model_dump())
    with pytest.raises(ValidationError, match="https"):
        LLMSettings(deepseek_base_url="http://api.deepseek.com")
    assert LLMSettings(deepseek_base_url="http://localhost:9999").deepseek_base_url == "http://localhost:9999"
