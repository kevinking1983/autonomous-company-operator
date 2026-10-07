"""OpenAI-compatible chat-completions adapter: Groq, OpenRouter, Ollama, vLLM, OpenAI and others."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from company_operator.llm.base import (
    FunctionCall,
    LLMError,
    LLMRequest,
    LLMResponse,
    Pacer,
    RetryableError,
    Usage,
)
from company_operator.llm.resilient import ModelChain, Notify

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def build_payload(model: str, request: LLMRequest) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
    for image in request.images:
        data = base64.b64encode(image.path.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:{image.mime_type};base64,{data}"}})
    payload: dict[str, Any] = {
        "model": model,
        "temperature": request.temperature,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": content if request.images else request.prompt},
        ],
    }
    if request.tools and request.tool_mode != "none":
        tools = [t for t in request.tools if not request.allowed_tools or t.name in request.allowed_tools]
        payload["tools"] = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
            }
            for t in tools
        ]
        payload["tool_choice"] = "required" if request.tool_mode == "any" else "auto"
    return payload


def parse_response(model: str, body: dict[str, Any], latency: float) -> LLMResponse:
    choices = body.get("choices") or []
    if not choices:
        raise RetryableError("empty response")
    message = choices[0].get("message", {})
    calls = []
    for call in message.get("tool_calls") or []:
        raw = call.get("function", {}).get("arguments") or "{}"
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise RetryableError(f"malformed tool arguments: {raw[:120]}") from exc
        calls.append(FunctionCall(name=call["function"]["name"], arguments=arguments or {}))
    usage = body.get("usage", {})
    return LLMResponse(
        text=message.get("content") or "",
        calls=calls,
        model=body.get("model", model),
        usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
        latency_s=latency,
    )


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        models: list[str],
        *,
        timeout: float = 90.0,
        min_interval: float = 0.0,
        transport: httpx.AsyncBaseTransport | None = None,
        attempts_per_model: int = 3,
        base_delay: float = 2.0,
        notify: Notify | None = None,
    ) -> None:
        if not base_url:
            raise LLMError("No base URL configured (set ACO_LLM_BASE_URL)")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )
        self._pacer = Pacer(min_interval)
        self._chain = ModelChain(
            models, self._attempt, attempts_per_model=attempts_per_model, base_delay=base_delay, notify=notify
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return await self._chain.generate(request)

    async def _attempt(self, model: str, request: LLMRequest) -> LLMResponse:
        await self._pacer.wait()
        started = time.monotonic()
        try:
            response = await self._http.post("/chat/completions", json=build_payload(model, request))
        except httpx.TimeoutException as exc:
            raise RetryableError(f"timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise RetryableError(f"connection failed: {exc}") from exc
        if response.status_code in RETRYABLE_STATUS:
            retry_after = response.headers.get("retry-after")
            raise RetryableError(
                f"HTTP {response.status_code}: {response.text[:200]}",
                float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else None,
            )
        if response.status_code >= 400:
            raise LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
        return parse_response(model, response.json(), time.monotonic() - started)

    async def aclose(self) -> None:
        await self._http.aclose()
