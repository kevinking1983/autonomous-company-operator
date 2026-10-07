"""Endpoints the dashboard reads: runs, pages seen, approval context, overview, activity, Company Pack."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from company_operator.api import app as api
from company_operator.audit.log import EventLog
from company_operator.memory.store import Episode, OperatorDB
from company_operator.policy import PolicyEngine
from company_operator.runtime import RunStore, TaskRequest
from company_operator.runtime.models import Observation, ToolCall
from company_operator.tools.human import HumanChannel


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db = OperatorDB(tmp_path / "operator.db")
    store = RunStore(tmp_path / "runs")
    api.app.dependency_overrides[api.get_db] = lambda: db
    api.app.dependency_overrides[api.get_store] = lambda: store
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def deps() -> tuple[OperatorDB, RunStore]:
    return api.app.dependency_overrides[api.get_db](), api.app.dependency_overrides[api.get_store]()


def page(seq: int, url: str, title: str, body: str) -> Observation:
    return Observation(
        seq=seq, phase="understand", step_id=None, call=ToolCall(tool="browser_open"), ok=True,
        output=f"URL: {url}\nTitle: {title}\n{body}",
    )  # fmt: skip


def make_run(store: RunStore) -> str:
    state = store.create(TaskRequest(text="Resolve support ticket TKT-1003.", ticket_id="TKT-1003"))
    state.observations = [
        page(1, "http://x/support/tickets/TKT-1003", "Ticket", "old"),
        page(2, "http://x/ops/orders/QB-1?tab=log", "Order", "packed"),
        page(3, "http://x/support/tickets/TKT-1003", "Ticket", "new"),
        Observation(
            seq=4, phase="execute", step_id="s1", call=ToolCall(tool="browser_fill"), ok=True, output="Filled"
        ),
    ]
    store.save(state)
    log = EventLog(store.run_dir(state.run_id) / "events.jsonl")
    log.emit("tool.call", tool="browser_open", arguments={})
    log.emit("contract.created", criteria=[])
    return state.run_id


def test_runs_and_pages(client: TestClient) -> None:
    run_id = make_run(deps()[1])
    [run] = client.get("/runs").json()
    assert run["run_id"] == run_id and run["request"]["ticket_id"] == "TKT-1003"
    pages = client.get(f"/runs/{run_id}/pages").json()
    # Latest version of each page, most recent first, query strings dropped.
    assert [(p["url"], p["seq"]) for p in pages] == [
        ("http://x/support/tickets/TKT-1003", 3),
        ("http://x/ops/orders/QB-1", 2),
    ]
    assert pages[0]["title"] == "Ticket" and "new" in pages[0]["text"]
    assert client.get("/runs/nope/pages").status_code == 404
    assert client.get(f"/runs/{run_id}/live.jpg").status_code == 404  # no browser action yet
    (deps()[1].run_dir(run_id) / "live.jpg").write_bytes(b"\xff\xd8frame")
    live = client.get(f"/runs/{run_id}/live.jpg")
    assert live.content == b"\xff\xd8frame" and live.headers["cache-control"] == "no-store"


def test_request_context_shows_what_the_operator_saw(client: TestClient) -> None:
    db, store = deps()
    run_id = make_run(store)
    evidence = store.run_dir(run_id) / "evidence"
    evidence.mkdir()
    log = EventLog()
    channel = HumanChannel(PolicyEngine(api.get_pack(), log), log, db, run_id=run_id)
    decision = channel.policy.evaluate(
        "payments.refund",
        {"payment_id": "PAY-1", "amount": 605.85, "reason": "wrong_order", "full_order_refund": True},
    )
    request = channel.request_approval(decision, "Bag swap")
    (evidence / f"approval-{request.id}.png").write_bytes(b"\x89PNG")

    context = client.get(f"/requests/{request.id}/context").json()
    assert context["request"]["id"] == request.id and context["run"]["run_id"] == run_id
    assert context["evidence"] == [f"approval-{request.id}.png"]
    assert len(context["pages"]) == 2
    assert client.get("/requests/H-9999/context").status_code == 404


def test_overview_counts_money_and_verification(client: TestClient) -> None:
    db, _ = deps()
    refund = {"action": "payments.refund", "facts": {"amount": 300.5}}
    coupon = {"action": "payments.issue_coupon", "facts": {"amount": 75}}
    db.add_episode(
        Episode("r1", "late_delivery", "a", "completed", "", [coupon], True, "2026-01-01T00:00:00")
    )
    db.add_episode(Episode("r2", "wrong_item", "b", "completed", "", [refund], False, "2026-01-02T00:00:00"))
    db.add_episode(Episode("r3", "wrong_item", "c", "escalated", "", [], False, "2026-01-03T00:00:00"))
    client.post("/tasks", json={"ticket_id": "TKT-1001"})
    overview = client.get("/overview").json()
    assert overview["runs_finished"] == 3 and overview["verified"] == 1
    assert overview["verified_rate"] == 0.333
    assert overview["outcomes"] == {"completed": 2, "escalated": 1}
    assert (overview["refunded"], overview["coupons"]) == (300.5, 75)
    assert overview["tasks"] == {"queued": 1} and overview["waiting_for_people"] == 0
    assert overview["approval_rate"] is None and overview["avg_minutes"] is None
    assert overview["auto_resolution_rate"] == 0.667  # r1 and r2 completed with nobody stepping in


def test_overview_times_and_approval_rate(client: TestClient) -> None:
    db, store = deps()
    state = store.create(TaskRequest(text="Resolve support ticket TKT-1003.", ticket_id="TKT-1003"))
    state.phase = "completed"
    state.created_at = "2026-10-07T10:00:00+00:00"
    store.save(state)  # save stamps updated_at; set it afterwards to a known value
    path = store.run_dir(state.run_id) / "state.json"
    path.write_text(path.read_text().replace(state.updated_at, "2026-10-07T10:30:00+00:00"))
    log = EventLog()
    channel = HumanChannel(PolicyEngine(api.get_pack(), log), log, db, run_id=state.run_id)
    facts = {"payment_id": "PAY-1", "amount": 605.85, "reason": "wrong_order", "full_order_refund": True}
    for approved in (True, False, True):
        request = channel.request_approval(channel.policy.evaluate("payments.refund", facts), "x")
        channel.decide(request.id, approved=approved, approver="p", note="no")
    overview = client.get("/overview").json()
    assert overview["approvals_decided"] == 3 and overview["approval_rate"] == 0.667
    assert overview["avg_minutes"] == 30.0 and 0 <= overview["avg_waiting_minutes"] < 1


def test_activity_keeps_notable_events(client: TestClient) -> None:
    run_id = make_run(deps()[1])
    [item] = client.get("/activity").json()
    assert item["type"] == "contract.created" and item["run_id"] == run_id and item["label"] == "TKT-1003"


def test_company_pack_hides_credentials(client: TestClient) -> None:
    pack = client.get("/company-pack").json()
    assert pack["company"] and pack["sops"] and pack["actions"]
    assert "sandbox_default" not in str(pack["systems"])
    assert "path" not in pack["sops"][0]


def test_server_mounts_api_and_serves_dashboard() -> None:
    server = TestClient(api.create_server())
    assert server.get("/api/health").json()["status"] == "ok"
    dist = Path(api.__file__).resolve().parents[2] / "dashboard" / "dist"
    if (dist / "index.html").exists():
        assert '<div id="root">' in server.get("/runs/some-run").text  # client-side route
        assert "[project]" not in server.get("/..%2Fpyproject.toml").text  # never outside dist
