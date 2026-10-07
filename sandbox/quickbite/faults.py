"""Fault-injection switchboard.

Real company systems are flaky: requests fail, sessions expire, someone ships a
UI change, and a payment call times out *after* the money has moved. The
operator has to cope with all of that, so the sandbox can produce each failure
on demand and reproducibly.

Faults are configured through the control API (never visible to the operator)
and every injected fault is logged, so an eval run can line up what went wrong
with how the operator reacted.
"""

from __future__ import annotations

import asyncio
import random
import threading
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Literal

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from sandbox.quickbite.db import now_iso

SYSTEMS = ("support", "ops", "payments")
Layout = Literal["standard", "shifted"]


@dataclass
class FaultConfig:
    # Which systems the faults below apply to.
    systems: list[str] = field(default_factory=lambda: list(SYSTEMS))
    # Probability that any page request returns HTTP 500.
    error_rate: float = 0.0
    # The next N page requests return HTTP 500, then behaviour returns to normal.
    fail_next: int = 0
    # Extra latency added to every request.
    latency_ms: int = 0
    # Every staff session is invalidated after this many more requests (0 = off).
    session_expiry_in: int = 0
    # The next N form submissions are rejected as "expired form", with no state change.
    stale_form_next: int = 0
    # The next N refund confirmations commit the refund, then respond 504 Gateway Timeout.
    refund_commit_timeout_next: int = 0
    # "shifted" renames and moves the main action buttons, as after a UI redesign.
    layout: Layout = "standard"
    # Seed for error_rate, so a run with random failures can be reproduced exactly.
    seed: int = 7


@dataclass
class FaultEvent:
    at: str
    kind: str
    system: str
    path: str


class FaultInjector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.config = FaultConfig()
        self.log: list[FaultEvent] = []
        self._rng = random.Random(self.config.seed)

    def configure(self, config: FaultConfig) -> None:
        with self._lock:
            self.config = config
            self._rng = random.Random(config.seed)

    def reset(self) -> None:
        self.configure(FaultConfig())
        with self._lock:
            self.log.clear()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {"config": asdict(self.config), "log": [asdict(e) for e in self.log]}

    def layout_for(self, system: str) -> Layout:
        return self.config.layout if system in self.config.systems else "standard"

    def take(self, counter: str, system: str, path: str) -> bool:
        """Consume one unit of a countdown fault, if it is armed for this system."""
        with self._lock:
            if system not in self.config.systems or getattr(self.config, counter) <= 0:
                return False
            setattr(self.config, counter, getattr(self.config, counter) - 1)
            self._record(counter, system, path)
            return True

    def _record(self, kind: str, system: str, path: str) -> None:
        self.log.append(FaultEvent(at=now_iso(), kind=kind, system=system, path=path))

    def _random_error(self, system: str, path: str) -> bool:
        with self._lock:
            if system in self.config.systems and self._rng.random() < self.config.error_rate:
                self._record("error_rate", system, path)
                return True
            return False

    def _session_expiry_due(self, system: str, path: str) -> bool:
        with self._lock:
            if system not in self.config.systems or self.config.session_expiry_in <= 0:
                return False
            self.config.session_expiry_in -= 1
            if self.config.session_expiry_in == 0:
                self._record("session_expiry", system, path)
                return True
            return False

    def middleware(
        self, expire_sessions: Callable[[], None]
    ) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
        async def apply(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            path = request.url.path
            system = path.strip("/").split("/", 1)[0]
            if system not in SYSTEMS or "/static/" in path:
                return await call_next(request)

            if self.config.latency_ms and system in self.config.systems:
                await asyncio.sleep(self.config.latency_ms / 1000)
            if self.take("fail_next", system, path) or self._random_error(system, path):
                return HTMLResponse(SERVER_ERROR_PAGE, status_code=500)
            if self._session_expiry_due(system, path):
                expire_sessions()
            if (
                request.method == "POST"
                and not path.endswith("/login")
                and self.take("stale_form_next", system, path)
            ):
                return HTMLResponse(STALE_FORM_PAGE, status_code=409)
            return await call_next(request)

        return apply


SERVER_ERROR_PAGE = """<!doctype html><html lang="en"><head><title>500 Internal Server Error</title></head>
<body><main><h1>500 Internal Server Error</h1>
<p>The server encountered a temporary error and could not complete your request.
Please try again in a moment.</p></main></body></html>"""

STALE_FORM_PAGE = """<!doctype html><html lang="en"><head><title>Form expired</title></head>
<body><main><h1>This form has expired</h1>
<p role="alert">Your changes were NOT saved. The page was open too long. Go back, reload the page
and submit the form again.</p></main></body></html>"""
