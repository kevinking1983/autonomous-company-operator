"""The approval inbox and memory API."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from company_operator.api import app as api
from company_operator.audit.log import EventLog
from company_operator.memory.store import OperatorDB
from company_operator.policy import PolicyEngine
from company_operator.tools.human import HumanChannel


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db = OperatorDB(tmp_path / "operator.db")
    api.app.dependency_overrides[api.get_db] = lambda: db
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def open_approval(client: TestClient) -> str:
    db: OperatorDB = api.app.dependency_overrides[api.get_db]()
    log = EventLog()
    channel = HumanChannel(PolicyEngine(api.get_pack(), log), log, db, run_id="r1")
    decision = channel.policy.evaluate(
        "payments.refund",
        {"payment_id": "PAY-1", "amount": 605.85, "reason": "wrong_order", "full_order_refund": True},
    )
    return channel.request_approval(decision, "Bag swap with QB-48244").id


def test_inbox_lists_pending_approvals(client: TestClient) -> None:
    request_id = open_approval(client)
    [item] = client.get("/requests").json()
    assert item["id"] == request_id and item["action"] == "payments.refund"
    assert set(item["rules"]) == {"high_value", "full_order_refund"}
    assert item["facts"]["amount"] == 605.85


def test_approve(client: TestClient) -> None:
    request_id = open_approval(client)
    response = client.post(
        f"/requests/{request_id}/decision", json={"approved": True, "approver": "priya.supervisor"}
    )
    assert response.json()["status"] == "approved"
    assert client.get("/requests").json() == []
    again = client.post(f"/requests/{request_id}/decision", json={"approved": False, "approver": "x"})
    assert again.status_code == 409


def test_reject_and_teach(client: TestClient) -> None:
    request_id = open_approval(client)
    client.post(
        f"/requests/{request_id}/decision",
        json={
            "approved": False,
            "approver": "priya.supervisor",
            "note": "Ask for a photo of the bag first.",
            "remember": True,
        },
    )
    learned = [f for f in client.get("/memory/facts").json() if f["learned"]]
    assert [f["text"] for f in learned] == ["Ask for a photo of the bag first."]
    client.delete(f"/memory/facts/{learned[0]['id'].removeprefix('learned-')}")
    assert not [f for f in client.get("/memory/facts").json() if f["learned"]]


def test_unknown_request(client: TestClient) -> None:
    assert client.get("/requests/H-9999").status_code == 404
    assert (
        client.post("/requests/H-9999/decision", json={"approved": True, "approver": "x"}).status_code == 404
    )
