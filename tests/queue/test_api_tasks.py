"""Task, run, evidence and live-stream endpoints."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from company_operator.api import app as api
from company_operator.audit.log import EventLog
from company_operator.memory.store import OperatorDB
from company_operator.runtime import RunStore, TaskRequest


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db = OperatorDB(tmp_path / "operator.db")
    store = RunStore(tmp_path / "runs")
    api.app.dependency_overrides[api.get_db] = lambda: db
    api.app.dependency_overrides[api.get_store] = lambda: store
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def store_of() -> RunStore:
    store: RunStore = api.app.dependency_overrides[api.get_store]()
    return store


def test_create_list_cancel(client: TestClient) -> None:
    created = client.post("/tasks", json={"ticket_id": "TKT-1001", "priority": "high"}).json()
    assert created["text"] == "Resolve support ticket TKT-1001." and created["priority"] == 2
    assert (
        client.post("/tasks", json={"ticket_id": "TKT-1001"}).json()["id"] == created["id"]
    )  # de-duplicated
    batch = client.post("/tasks", json={"text": "Clear today's late-delivery tickets"}).json()
    assert batch["source"] == "supervisor"
    assert [t["id"] for t in client.get("/tasks").json()] == [batch["id"], created["id"]]
    assert client.post(f"/tasks/{batch['id']}/cancel").json()["status"] == "cancelled"
    assert client.get("/stats").json()["tasks"] == {"cancelled": 1, "queued": 1}
    assert client.post("/tasks", json={}).status_code == 422


def test_run_report_events_and_evidence(client: TestClient) -> None:
    store = store_of()
    state = store.create(TaskRequest(text="Resolve support ticket TKT-1001.", ticket_id="TKT-1001"))
    run_dir = store.run_dir(state.run_id)
    log = EventLog(run_dir / "events.jsonl")
    log.emit("phase", **{"from": "understand", "to": "plan"})
    log.emit("tool.call", tool="browser_open", arguments={})
    (run_dir / "evidence").mkdir()
    (run_dir / "evidence" / "verify1-01-order-QB-48213.png").write_bytes(b"\x89PNG")

    assert client.get(f"/runs/{state.run_id}").json()["request"]["ticket_id"] == "TKT-1001"
    assert client.get(f"/runs/{state.run_id}/report").json()["evidence"] == ["verify1-01-order-QB-48213.png"]
    assert [e["type"] for e in client.get(f"/runs/{state.run_id}/events?after=1").json()] == ["tool.call"]
    assert client.get(f"/runs/{state.run_id}/evidence/verify1-01-order-QB-48213.png").content == b"\x89PNG"
    assert client.get(f"/runs/{state.run_id}/evidence/..%2Fstate.json").status_code == 404
    assert client.get("/runs/nope/report").status_code == 404


def test_live_stream_sends_events_then_ends(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "STREAM_POLL_SECONDS", 0.01)
    monkeypatch.setattr(api, "STREAM_IDLE_SECONDS", 0.05)
    store = store_of()
    state = store.create(TaskRequest(text="x"))
    state.phase = "completed"
    store.save(state)
    EventLog(store.run_dir(state.run_id) / "events.jsonl").emit("run.completed", summary="done")
    with client.stream("GET", f"/runs/{state.run_id}/stream") as response:
        body = "".join(response.iter_text())
    assert "event: run.completed" in body and body.rstrip().endswith("event: end\ndata: {}")


def test_bulk_queue(client: TestClient) -> None:
    single = client.post("/tasks", json={"ticket_id": "TKT-1001"}).json()
    tasks = client.post(
        "/tasks/bulk",
        json={"ticket_ids": ["tkt-1002", "TKT-1001", " TKT-1002 ", "TKT-1003"], "priority": "high"},
    ).json()
    assert [t["ticket_id"] for t in tasks] == ["TKT-1002", "TKT-1001", "TKT-1003"]
    assert tasks[1]["id"] == single["id"]  # already queued: not queued twice
    assert {t["priority"] for t in (tasks[0], tasks[2])} == {2}
    assert client.post("/tasks/bulk", json={"ticket_ids": ["TKT-1", "drop table"]}).status_code == 422
    assert client.post("/tasks/bulk", json={"ticket_ids": []}).status_code == 422
