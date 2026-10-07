"""Provider-agnostic model client: Gemini and OpenAI-compatible adapters with retries and model fallback."""

from company_operator.llm.base import (
    FunctionCall,
    ImagePart,
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
    RetryableError,
    ToolSpec,
    Usage,
)
from company_operator.llm.factory import build_llm

__all__ = [
    "FunctionCall",
    "ImagePart",
    "LLMClient",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "RetryableError",
    "ToolSpec",
    "Usage",
    "build_llm",
]
