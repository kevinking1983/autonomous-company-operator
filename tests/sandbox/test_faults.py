"""The fault-injection switchboard and the control API."""

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from sandbox.quickbite.db import Database
from tests.sandbox.conftest import CONTROL


def set_faults(client: TestClient, **config: Any) -> None:
    assert client.put("/_control/faults", json=config, headers=CONTROL).status_code == 200


def test_control_api_requires_key(client: TestClient) -> None:
    assert client.post("/_control/reset").status_code == 403
    assert client.post("/_control/reset", headers={"X-Control-Key": "wrong"}).status_code == 403


def test_fail_next_then_recover(login: Callable[..., TestClient], client: TestClient) -> None:
    login("support")
    set_faults(client, fail_next=2)
    assert client.get("/support/tickets").status_code == 500
    assert client.get("/support/tickets").status_code == 500
    assert client.get("/support/tickets").status_code == 200
    log = client.get("/_control/faults", headers=CONTROL).json()["log"]
    assert [e["kind"] for e in log] == ["fail_next", "fail_next"]


def test_faults_can_target_one_system(login: Callable[..., TestClient], client: TestClient) -> None:
    login("support", "ops")
    set_faults(client, fail_next=1, systems=["ops"])
    assert client.get("/support/tickets").status_code == 200
    assert client.get("/ops/orders").status_code == 500


def test_stale_form_rejects_without_saving(
    login: Callable[..., TestClient], client: TestClient, db: Database
) -> None:
    login("support")
    set_faults(client, stale_form_next=1)
    response = client.post("/support/tickets/TKT-1001/note", data={"body": "first try"})
    assert response.status_code == 409
    assert db.one("SELECT 1 FROM support_messages WHERE body = 'first try'") is None
    client.post("/support/tickets/TKT-1001/note", data={"body": "second try"})
    assert db.one("SELECT 1 FROM support_messages WHERE body = 'second try'") is not None


def test_session_expiry_forces_login(login: Callable[..., TestClient], client: TestClient) -> None:
    login("support")
    set_faults(client, session_expiry_in=2)
    assert client.get("/support/tickets").url.path == "/support/tickets"
    assert client.get("/support/tickets").url.path == "/support/login"


def test_refund_commits_then_times_out(
    login: Callable[..., TestClient], client: TestClient, db: Database
) -> None:
    login("payments")
    set_faults(client, refund_commit_timeout_next=1)
    payment = db.one("SELECT id FROM pay_payments WHERE order_id = 'QB-48260'")
    assert payment is not None
    response = client.post(
        f"/payments/transactions/{payment['id']}/refund/confirm",
        data={"amount": "100", "reason_code": "order_cancelled"},
    )
    assert response.status_code == 504
    # The browser saw a failure, but the money moved.
    assert db.one("SELECT 1 FROM pay_refunds WHERE order_id = 'QB-48260'") is not None


def test_shifted_layout_renames_buttons(
    login: Callable[..., TestClient], client: TestClient, db: Database
) -> None:
    login("payments")
    payment = db.one("SELECT id FROM pay_payments WHERE order_id = 'QB-48213'")
    assert payment is not None
    assert "Review refund" in client.get(f"/payments/transactions/{payment['id']}").text
    set_faults(client, layout="shifted")
    page = client.get(f"/payments/transactions/{payment['id']}").text
    assert "Review refund" not in page and "Process refund" in page and "Payment actions" in page


def test_reset_restores_world_and_clears_faults(
    login: Callable[..., TestClient], client: TestClient, db: Database
) -> None:
    login("support")
    client.post("/support/tickets/TKT-1001/update", data={"status": "closed"})
    set_faults(client, error_rate=1.0)
    assert client.post("/_control/reset", headers=CONTROL).status_code == 200
    ticket = db.one("SELECT status FROM support_tickets WHERE id = 'TKT-1001'")
    assert ticket is not None and ticket["status"] == "open"
    assert client.get("/_control/faults", headers=CONTROL).json()["config"]["error_rate"] == 0.0


def test_customer_message_injection(client: TestClient, db: Database) -> None:
    response = client.post(
        "/_control/tickets/TKT-1008/customer-message", json={"body": "Any update?"}, headers=CONTROL
    )
    assert response.status_code == 200
    assert db.one("SELECT 1 FROM support_messages WHERE ticket_id = 'TKT-1008' AND body = 'Any update?'")
