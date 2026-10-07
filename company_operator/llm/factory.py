"""Build the configured model client."""

from __future__ import annotations

from company_operator.config import Settings
from company_operator.llm.base import LLMClient, LLMError
from company_operator.llm.gemini import GeminiClient
from company_operator.llm.openai_compat import OpenAICompatibleClient
from company_operator.llm.resilient import Notify


def build_llm(settings: Settings, notify: Notify | None = None) -> LLMClient:
    models = settings.model_list
    common = {"timeout": settings.llm_timeout, "min_interval": settings.llm_min_interval, "notify": notify}
    match settings.llm_provider:
        case "gemini":
            return GeminiClient(settings.llm_api_key, models, **common)  # type: ignore[arg-type]
        case "openai_compatible":
            return OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key, models, **common)  # type: ignore[arg-type]
    raise LLMError(f"Unknown ACO_LLM_PROVIDER {settings.llm_provider!r}; use 'gemini' or 'openai_compatible'")
