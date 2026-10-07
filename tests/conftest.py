"""Shared fixtures: a live QuickBite sandbox server for tests that drive a real browser."""

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from sandbox.quickbite.app import create_app

CONTROL_KEY = "test-control"


class LiveSandbox:
    def __init__(self, url: str) -> None:
        self.url = url
        self.control = httpx.Client(base_url=url, headers={"X-Control-Key": CONTROL_KEY})

    def reset(self) -> None:
        self.control.post("/_control/reset").raise_for_status()

    def faults(self, **config: object) -> None:
        self.control.put("/_control/faults", json=config).raise_for_status()

    def rows(self, table: str) -> list[dict[str, object]]:
        response = self.control.get(f"/_control/state/{table}")
        response.raise_for_status()
        rows: list[dict[str, object]] = response.json()
        return rows


@pytest.fixture(scope="session")
def live_sandbox(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveSandbox]:
    db_path: Path = tmp_path_factory.mktemp("sandbox") / "quickbite.db"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(
        create_app(db_path, control_key=CONTROL_KEY), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Sandbox server did not start")
        time.sleep(0.05)
    yield LiveSandbox(f"http://127.0.0.1:{port}")
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def sandbox(live_sandbox: LiveSandbox) -> LiveSandbox:
    """The live sandbox, reset to its seeded state with no faults."""
    live_sandbox.reset()
    return live_sandbox
