"""Retries with backoff, fallback to the next model, and cooldown for rate-limited models.

Free-tier models are often overloaded ("high demand", HTTP 503) or out of
quota (HTTP 429). A run should slow down rather than fail:

* each model gets a few attempts with exponential backoff (honouring any
  retry-after hint) before the next model in the list is tried;
* a model that reports it is out of quota is put on cooldown for the
  suggested time, so the following calls go straight to the next model
  instead of waiting on it again.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from company_operator.llm.base import LLMError, LLMRequest, LLMResponse, RetryableError

Attempt = Callable[[str, LLMRequest], Awaitable[LLMResponse]]
Notify = Callable[[str, dict[str, object]], None]
MIN_COOLDOWN = 20.0
MAX_WAIT = 120.0  # never sleep longer than this for a model; fail instead (e.g. a daily quota is used up)


class ModelChain:
    def __init__(
        self,
        models: list[str],
        attempt: Attempt,
        *,
        attempts_per_model: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 30.0,
        notify: Notify | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not models:
            raise ValueError("At least one model is required")
        self.models = models
        self.attempt = attempt
        self.attempts_per_model = attempts_per_model
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.notify = notify or (lambda event, data: None)
        self.clock = clock
        self._cool_until: dict[str, float] = {}

    def _order(self) -> list[str]:
        """Models not cooling down first (in configured order), then the rest by soonest available."""
        now = self.clock()
        ready = [m for m in self.models if self._cool_until.get(m, 0) <= now]
        cooling = sorted((m for m in self.models if m not in ready), key=lambda m: self._cool_until[m])
        return ready + cooling

    async def generate(self, request: LLMRequest) -> LLMResponse:
        errors: list[str] = []
        order = self._order()
        for index, model in enumerate(order):
            wait = self._cool_until.get(model, 0) - self.clock()
            if wait > MAX_WAIT:
                errors.append(f"{model}: out of quota for another {wait / 60:.0f} min")
                continue
            if wait > 0:  # every model is cooling down: wait for the one that is free soonest
                self.notify("llm.wait", {"model": model, "seconds": round(wait, 1)})
                await asyncio.sleep(wait)
            for attempt in range(1, self.attempts_per_model + 1):
                try:
                    return await self.attempt(model, request)
                except RetryableError as exc:
                    errors.append(f"{model} #{attempt}: {exc}")
                    has_alternative = index < len(order) - 1
                    long_wait = (exc.retry_after or 0) > MAX_WAIT
                    if is_quota(exc) and (has_alternative or long_wait):
                        cooldown = max(MIN_COOLDOWN, exc.retry_after or 0)
                        self._cool_until[model] = self.clock() + cooldown
                        self.notify(
                            "llm.cooldown", {"model": model, "seconds": cooldown, "error": str(exc)[:160]}
                        )
                        break
                    if attempt == self.attempts_per_model:
                        self.notify("llm.fallback", {"model": model, "error": str(exc)[:200]})
                        break
                    delay = min(self.max_delay, exc.retry_after or self.base_delay * 2 ** (attempt - 1))
                    self.notify(
                        "llm.retry",
                        {"model": model, "attempt": attempt, "delay": delay, "error": str(exc)[:200]},
                    )
                    await asyncio.sleep(delay)
                except (
                    LLMError
                ) as exc:  # not retryable on this model (e.g. model not available): try the next
                    errors.append(f"{model}: {exc}")
                    self.notify("llm.fallback", {"model": model, "error": str(exc)[:200]})
                    break
        raise LLMError("All models failed: " + " | ".join(errors[-6:]))


def is_quota(exc: RetryableError) -> bool:
    return "429" in str(exc) or "quota" in str(exc).lower()
