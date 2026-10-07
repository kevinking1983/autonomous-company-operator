"""Provider adapters: wire format, retries and model fallback (no network: canned HTTP responses)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from company_operator.llm import ImagePart, LLMError, LLMRequest, ToolSpec
from company_operator.llm.gemini import GeminiClient
from company_operator.llm.gemini import build_payload as gemini_payload
from company_operator.llm.openai_compat import OpenAICompatibleClient

REFUND_TOOL = ToolSpec("refund", "Refund", {"type": "object", "properties": {"amount": {"type": "number"}}})


def gemini_ok(call: dict[str, Any] | None = None, text: str = "") -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if call:
        parts.append({"functionCall": call, "thoughtSignature": "abc"})
    if text:
        parts.append({"text": text})
    return {
        "candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 7},
        "modelVersion": "gemini-test",
    }


class Recorder:
    """An httpx transport that replays queued responses and records requests."""

    def __init__(self, *responses: tuple[int, dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def test_gemini_payload_shape(tmp_path: Path) -> None:
    image = tmp_path / "bag.png"
    image.write_bytes(b"\x89PNG fake")
    payload = gemini_payload(
        LLMRequest(
            system="sys",
            prompt="hi",
            images=[ImagePart(image)],
            tools=[REFUND_TOOL],
            tool_mode="any",
            allowed_tools=["refund"],
        )
    )
    assert payload["systemInstruction"]["parts"][0]["text"] == "sys"
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"text": "hi"} and parts[1]["inlineData"]["mimeType"] == "image/png"
    declaration = payload["tools"][0]["functionDeclarations"][0]
    assert declaration["parametersJsonSchema"] == REFUND_TOOL.parameters
    assert payload["toolConfig"]["functionCallingConfig"] == {
        "mode": "ANY",
        "allowedFunctionNames": ["refund"],
    }


def test_gemini_text_only_request_has_no_tools() -> None:
    payload = gemini_payload(LLMRequest(system="s", prompt="p", tools=[REFUND_TOOL], tool_mode="none"))
    assert "tools" not in payload and "toolConfig" not in payload


async def test_gemini_parses_calls_and_usage() -> None:
    rec = Recorder((200, gemini_ok({"name": "refund", "args": {"amount": 60}}, text="ok")))
    client = GeminiClient("key", ["m1"], transport=rec.transport)
    response = await client.generate(LLMRequest(system="s", prompt="p", tools=[REFUND_TOOL]))
    assert [(c.name, c.arguments) for c in response.calls] == [("refund", {"amount": 60})]
    assert response.text == "ok" and response.usage.input_tokens == 100 and response.model == "gemini-test"
    assert rec.requests[0].headers["x-goog-api-key"] == "key"


async def test_gemini_ignores_thought_text() -> None:
    body = gemini_ok()
    body["candidates"][0]["content"]["parts"] = [{"text": "thinking...", "thought": True}, {"text": "answer"}]
    client = GeminiClient("key", ["m1"], transport=Recorder((200, body)).transport)
    assert (await client.generate(LLMRequest(system="s", prompt="p"))).text == "answer"


async def test_overloaded_model_is_retried_then_falls_back() -> None:
    busy = (503, {"error": {"code": 503, "message": "high demand"}})
    rec = Recorder(busy, busy, (200, gemini_ok(text="from backup")))
    client = GeminiClient(
        "key", ["primary", "backup"], transport=rec.transport, attempts_per_model=2, base_delay=0
    )
    response = await client.generate(LLMRequest(system="s", prompt="p"))
    assert response.text == "from backup"
    assert rec.paths() == [
        "/v1beta/models/primary:generateContent",
        "/v1beta/models/primary:generateContent",
        "/v1beta/models/backup:generateContent",
    ]


async def test_unavailable_model_falls_back_immediately() -> None:
    rec = Recorder((404, {"error": {"message": "no longer available"}}), (200, gemini_ok(text="ok")))
    client = GeminiClient("key", ["retired", "current"], transport=rec.transport, base_delay=0)
    assert (await client.generate(LLMRequest(system="s", prompt="p"))).text == "ok"
    assert len(rec.requests) == 2


async def test_retry_after_hint_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("company_operator.llm.resilient.asyncio.sleep", fake_sleep)
    limited = (429, {"error": {"message": "quota", "details": [{"retryDelay": "7s"}]}})
    client = GeminiClient("key", ["m"], transport=Recorder(limited, (200, gemini_ok(text="ok"))).transport)
    await client.generate(LLMRequest(system="s", prompt="p"))
    assert slept == [7.0]


async def test_malformed_function_call_is_retried() -> None:
    bad = {"candidates": [{"content": {"parts": []}, "finishReason": "MALFORMED_FUNCTION_CALL"}]}
    rec = Recorder((200, bad), (200, gemini_ok({"name": "refund", "args": {}})))
    client = GeminiClient("key", ["m"], transport=rec.transport, base_delay=0)
    assert (await client.generate(LLMRequest(system="s", prompt="p"))).calls[0].name == "refund"


async def test_all_models_failing_raises() -> None:
    busy = (503, {"error": {"message": "busy"}})
    client = GeminiClient(
        "key", ["a", "b"], transport=Recorder(busy, busy).transport, attempts_per_model=1, base_delay=0
    )
    with pytest.raises(LLMError, match="All models failed"):
        await client.generate(LLMRequest(system="s", prompt="p"))


def test_missing_key_is_a_clear_error() -> None:
    with pytest.raises(LLMError, match="ACO_LLM_API_KEY"):
        GeminiClient("", ["m"])


async def test_openai_compatible_round_trip() -> None:
    body = {
        "model": "llama-test",
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [{"function": {"name": "refund", "arguments": json.dumps({"amount": 60})}}],
                }
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 5},
    }
    rec = Recorder((200, body))
    client = OpenAICompatibleClient(
        "https://api.example.com/v1", "secret", ["llama"], transport=rec.transport
    )
    response = await client.generate(LLMRequest(system="s", prompt="p", tools=[REFUND_TOOL], tool_mode="any"))
    assert [(c.name, c.arguments) for c in response.calls] == [("refund", {"amount": 60})]
    sent = json.loads(rec.requests[0].content)
    assert sent["tool_choice"] == "required" and sent["messages"][0] == {"role": "system", "content": "s"}
    assert rec.requests[0].headers["authorization"] == "Bearer secret"


async def test_rate_limited_model_cools_down() -> None:
    quota = (
        429,
        {"error": {"message": "You exceeded your current quota", "details": [{"retryDelay": "40s"}]}},
    )
    rec = Recorder(quota, (200, gemini_ok(text="backup 1")), (200, gemini_ok(text="backup 2")))
    client = GeminiClient("key", ["primary", "backup"], transport=rec.transport, base_delay=0)
    assert (await client.generate(LLMRequest(system="s", prompt="p"))).text == "backup 1"
    # The next call skips the cooling primary instead of waiting on it again.
    assert (await client.generate(LLMRequest(system="s", prompt="p"))).text == "backup 2"
    assert [p.split("/")[-1] for p in rec.paths()] == [
        "primary:generateContent",
        "backup:generateContent",
        "backup:generateContent",
    ]


async def test_daily_quota_exhausted_fails_fast() -> None:
    daily = (
        429,
        {
            "error": {
                "message": "Quota exceeded for metric ...free_tier_requests, limit: 20. Please retry in 10h10m34.2s."
            }
        },
    )
    rec = Recorder(daily, daily)
    client = GeminiClient("key", ["a", "b"], transport=rec.transport, base_delay=0)
    with pytest.raises(LLMError, match="All models failed"):
        await client.generate(LLMRequest(system="s", prompt="p"))
    with pytest.raises(
        LLMError, match=r"out of quota for another 61\d min"
    ):  # both on long cooldown: no new requests
        await client.generate(LLMRequest(system="s", prompt="p"))
    assert len(rec.requests) == 2
