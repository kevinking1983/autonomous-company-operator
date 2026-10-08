"""Append-only event log.

Every tool call, policy decision, blocked request, retry and piece of evidence
becomes an event. The log is the single source of truth for what the operator
did: the dashboard, run reports and replay are all built from it.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Event:
    seq: int
    at: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)


Listener = Callable[[Event], None]


class EventLog:
    """In-memory event list, optionally mirrored line by line to a JSONL file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._events: list[Event] = []
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                # A resumed run (possibly in another process) carries on its own log: checks over the whole
                # run, like "no money moved twice", must see what happened before the pause too.
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        self._events.append(Event(**json.loads(line)))

    def emit(self, type: str, **data: Any) -> Event:
        with self._lock:
            event = Event(seq=len(self._events) + 1, at=_now(), type=type, data=data)
            self._events.append(event)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(event), default=str) + "\n")
            listeners = list(self._listeners)
        for listener in listeners:
            listener(event)
        return event

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    @property
    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def of_type(self, *types: str) -> list[Event]:
        return [e for e in self.events if e.type in types]
