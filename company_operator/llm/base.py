"""Provider-neutral model interface.

The brain speaks only these types. Each provider adapter translates them to
its own wire format, so switching provider is a configuration change.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ImagePart:
    path: Path
    mime_type: str = "image/png"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema of the arguments


@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMRequest:
    system: str
    prompt: str
    images: list[ImagePart] = field(default_factory=list)
    tools: list[ToolSpec] = field(default_factory=list)
    # "any": the model must answer with a function call; "none": plain text only.
    tool_mode: Literal["auto", "any", "none"] = "auto"
    allowed_tools: list[str] | None = None
    temperature: float = 0.2
    purpose: str = ""  # e.g. "next_action", for logs


@dataclass(frozen=True)
class LLMResponse:
    text: str
    calls: list[FunctionCall]
    model: str
    usage: Usage = Usage()
    latency_s: float = 0.0


class LLMError(RuntimeError):
    """The provider could not produce an answer (after retries and fallbacks)."""


class RetryableError(LLMError):
    """A temporary provider problem: overloaded, rate limited, timed out."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMClient(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse: ...


class Pacer:
    """Keeps calls at least `min_interval` seconds apart (free tiers limit requests per minute)."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self.min_interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()
