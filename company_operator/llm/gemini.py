"""Google Gemini adapter (REST generateContent API)."""

from __future__ import annotations

import base64
import re
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

API = "https://generativelanguage.googleapis.com/v1beta"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# Finish reasons that mean "this answer is unusable, ask again".
RETRYABLE_FINISH = {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL", "OTHER", "RECITATION"}


def build_payload(request: LLMRequest) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": request.prompt}]
    for image in request.images:
        data = base64.b64encode(image.path.read_bytes()).decode()
        parts.append({"inlineData": {"mimeType": image.mime_type, "data": data}})
    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": request.system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": request.temperature},
    }
    if request.tools and request.tool_mode != "none":
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {"name": t.name, "description": t.description, "parametersJsonSchema": t.parameters}
                    for t in request.tools
                ]
            }
        ]
        config: dict[str, Any] = {"mode": request.tool_mode.upper()}
        if request.allowed_tools and request.tool_mode == "any":
            config["allowedFunctionNames"] = request.allowed_tools
        payload["toolConfig"] = {"functionCallingConfig": config}
    return payload


def parse_response(model: str, body: dict[str, Any], latency: float) -> LLMResponse:
    candidates = body.get("candidates") or []
    if not candidates:
        reason = body.get("promptFeedback", {}).get("blockReason", "no candidates")
        raise RetryableError(f"empty response ({reason})")
    candidate = candidates[0]
    finish = candidate.get("finishReason", "")
    parts = candidate.get("content", {}).get("parts", [])
    calls = [
        FunctionCall(name=p["functionCall"]["name"], arguments=p["functionCall"].get("args") or {})
        for p in parts
        if "functionCall" in p
    ]
    text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
    if not calls and not text.strip() and finish in RETRYABLE_FINISH:
        raise RetryableError(f"unusable answer (finishReason={finish})")
    usage = body.get("usageMetadata", {})
    return LLMResponse(
        text=text,
        calls=calls,
        model=body.get("modelVersion", model),
        usage=Usage(usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)),
        latency_s=latency,
    )


def parse_duration(text: str) -> float | None:
    """Seconds in a duration like '36.5s', '10h10m34.2s' or 'retry in 2m'."""
    found = re.search(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?", text)
    if not found or not any(found.groups()):
        return None
    hours, minutes, seconds = found.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def _retry_after(body: dict[str, Any]) -> float | None:
    for detail in body.get("error", {}).get("details", []):
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and (seconds := parse_duration(delay)) is not None:
            return seconds
    message = body.get("error", {}).get("message", "")
    if found := re.search(r"retry in ([\dhms.]+)", message):
        return parse_duration(found.group(1))
    return None


class GeminiClient:
    def __init__(
        self,
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
        if not api_key:
            raise LLMError("No API key configured (set ACO_LLM_API_KEY)")
        self._http = httpx.AsyncClient(
            base_url=API, headers={"x-goog-api-key": api_key}, timeout=timeout, transport=transport
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
            response = await self._http.post(f"/models/{model}:generateContent", json=build_payload(request))
        except httpx.TimeoutException as exc:
            raise RetryableError(f"timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise RetryableError(f"connection failed: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code in RETRYABLE_STATUS:
            message = body.get("error", {}).get("message", response.text[:200])
            raise RetryableError(f"HTTP {response.status_code}: {message}", _retry_after(body))
        if response.status_code >= 400:
            raise LLMError(
                f"HTTP {response.status_code}: {body.get('error', {}).get('message', response.text[:300])}"
            )
        return parse_response(model, body, time.monotonic() - started)

    async def aclose(self) -> None:
        await self._http.aclose()
