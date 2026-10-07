"""Reliability lab endpoints: starting and following evals, and the live sandbox's fault switchboard."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from company_operator.api import app as api
from company_operator.evals.cases import Check
from company_operator.evals.harness import CaseResult, EvalStore
from company_operator.evals.world import Sandbox
from company_operator.runtime import RunStore, TaskRequest
from tests.conftest import CONTROL_KEY, LiveSandbox


@pytest.fixture
def evals(tmp_path: Path) -> Iterator[EvalStore]:
    store = EvalStore(tmp_path / "evals")
    launched: list[str] = []
    api.app.dependency_overrides[api.get_evals] = lambda: store
    # Pretend to start the eval process: this test process stands in for it (it is alive).
    api.app.dependency_overrides[api.get_launcher] = lambda: (
        lambda _dir, eval_id: launched.append(eval_id) or os.getpid()
    )
    yield store
    api.app.dependency_overrides.clear()


def test_catalog() -> None:
    catalog = TestClient(api.app).get("/evals/catalog").json()
    assert {s["id"] for s in catalog["suites"]} == {"smoke", "core", "reliability"}
    assert len(catalog["cases"]) == 14
    assert next(p for p in catalog["profiles"] if p["name"] == "refund_timeout")["applies_to"] == ["refund"]


def test_start_follow_stop(evals: EvalStore) -> None:
    client = TestClient(api.app)
    started = client.post("/evals", json={"suite": "smoke"}).json()
    assert started["status"] == "queued" and started["pid"] == os.getpid() and len(started["pairs"]) == 3
    assert client.post("/evals", json={"suite": "core"}).status_code == 409  # one at a time

    evals.append(
        started["id"],
        CaseResult("missing_item", "clean", "pass", [Check("money.amount", "x", True)], verified=True),
    )
    [listed] = client.get("/evals").json()
    assert (listed["done"], listed["total"], listed["passed"], listed["pass_rate"]) == (1, 3, 1, 1.0)
    detail = client.get(f"/evals/{started['id']}").json()
    assert detail["results"][0]["verifier_agrees"] is True and detail["scorecard"]["pass_rate"] == 1.0

    assert client.post(f"/evals/{started['id']}/stop").json()["status"] == "stopping"
    assert client.get("/evals/nope").status_code == 404
    assert client.post("/evals", json={"cases": ["nope"]}).status_code == 422


def test_dead_eval_process_is_noticed(evals: EvalStore) -> None:
    eval_id = evals.create([("missing_item", "clean")], label="x", models=[])
    evals.update(eval_id, status="running", pid=999_999_999)  # no such process
    client = TestClient(api.app)
    assert client.get(f"/evals/{eval_id}").json()["meta"]["status"] == "stopped"
    assert client.post(f"/evals/{eval_id}/resume").json()["status"] == "queued"


def test_fault_switchboard(sandbox: LiveSandbox) -> None:
    api.app.dependency_overrides[api.get_sandbox] = lambda: Sandbox(sandbox.url, CONTROL_KEY)
    try:
        client = TestClient(api.app)
        on = client.put("/sandbox/faults", json={"layout": "shifted", "latency_ms": 10}).json()
        assert on["config"]["layout"] == "shifted" and on["config"]["latency_ms"] == 10
        assert client.post("/sandbox/reset").json()["config"]["layout"] == "standard"
    finally:
        api.app.dependency_overrides.clear()
    api.app.dependency_overrides[api.get_sandbox] = lambda: Sandbox("http://127.0.0.1:9", "x", timeout=1)
    try:
        assert TestClient(api.app).get("/sandbox/faults").status_code == 502
    finally:
        api.app.dependency_overrides.clear()


def test_eval_runs_open_in_the_run_view(evals: EvalStore) -> None:
    eval_id = evals.create([("missing_item", "clean")], label="x", models=[])
    runs = RunStore(evals.dir(eval_id) / "runs" / "missing_item__clean" / "runs")
    state = runs.create(TaskRequest(text="Resolve support ticket TKT-1001.", ticket_id="TKT-1001"))
    client = TestClient(api.app)
    scope = f"eval:{eval_id}:missing_item:clean"
    assert (
        client.get(f"/runs/{state.run_id}", params={"scope": scope}).json()["request"]["ticket_id"]
        == "TKT-1001"
    )
    assert client.get(f"/runs/{state.run_id}/events", params={"scope": scope}).json() == []
    assert (
        client.get(f"/runs/{state.run_id}", params={"scope": "eval:nope:missing_item:clean"}).status_code
        == 404
    )
    assert client.get(f"/runs/{state.run_id}", params={"scope": "../../etc"}).status_code == 404
