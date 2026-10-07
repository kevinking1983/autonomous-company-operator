"""Behaviour of the three QuickBite back-office apps."""

from collections.abc import Callable

from fastapi.testclient import TestClient

from sandbox.quickbite.db import Database


def test_pages_require_login(client: TestClient) -> None:
    response = client.get("/payments/refunds?q=QB-1", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/payments/login?next=/payments/refunds%3Fq%3DQB-1"


def test_wrong_password_is_rejected(client: TestClient) -> None:
    response = client.post("/ops/login", data={"username": "ai.operator", "password": "nope"})
    assert response.status_code == 401
    assert "Invalid username or password" in response.text


def test_each_system_has_its_own_session(login: Callable[..., TestClient]) -> None:
    client = login("support")
    assert client.get("/support/tickets").url.path == "/support/tickets"
    assert client.get("/ops/orders").url.path == "/ops/login"


# ── Support Desk ──


def test_ticket_queue_lists_active_tickets(login: Callable[..., TestClient]) -> None:
    page = login("support").get("/support/tickets").text
    assert "TKT-1001" in page
    assert "TKT-0990" not in page  # resolved, so not in the active queue


def test_reply_and_status_change(login: Callable[..., TestClient], db: Database) -> None:
    client = login("support")
    client.post("/support/tickets/TKT-1001/reply", data={"body": "Refund issued.", "set_status": "resolved"})
    ticket = db.one("SELECT status FROM support_tickets WHERE id = 'TKT-1001'")
    assert ticket is not None and ticket["status"] == "resolved"
    assert "Refund issued." in client.get("/support/tickets/TKT-1001").text


def test_internal_note_and_triage(login: Callable[..., TestClient], db: Database) -> None:
    client = login("support")
    client.post("/support/tickets/TKT-1011/note", data={"body": "Duplicate of TKT-0995"})
    client.post(
        "/support/tickets/TKT-1011/update",
        data={"status": "closed", "category": "missing_item", "linked_ticket_id": "tkt-0995"},
    )
    ticket = db.one("SELECT * FROM support_tickets WHERE id = 'TKT-1011'")
    assert ticket is not None
    assert (ticket["status"], ticket["category"], ticket["linked_ticket_id"]) == (
        "closed",
        "missing_item",
        "TKT-0995",
    )
    note = db.one(
        "SELECT internal FROM support_messages WHERE ticket_id = 'TKT-1011' AND body LIKE 'Duplicate%'"
    )
    assert note is not None and note["internal"] == 1


def test_linking_to_missing_ticket_fails(login: Callable[..., TestClient]) -> None:
    response = login("support").post(
        "/support/tickets/TKT-1011/update", data={"linked_ticket_id": "TKT-9999"}
    )
    assert "does not exist" in response.text


def test_attachment_is_served(login: Callable[..., TestClient]) -> None:
    client = login("support")
    page = client.get("/support/tickets/TKT-1003").text
    assert "IMG_20261007_bag.png" in page
    response = client.get("/support/attachments/1")
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_scripted_customer_answers_after_agent_reply(login: Callable[..., TestClient], db: Database) -> None:
    db.execute("UPDATE support_scripted_replies SET delay_seconds = 0")
    client = login("support")
    assert "dal makhani was missing" not in client.get("/support/tickets/TKT-1012").text
    client.post(
        "/support/tickets/TKT-1012/reply", data={"body": "What went wrong?", "set_status": "pending_customer"}
    )
    assert "dal makhani was missing" in client.get("/support/tickets/TKT-1012").text
    ticket = db.one("SELECT status FROM support_tickets WHERE id = 'TKT-1012'")
    assert ticket is not None and ticket["status"] == "open"


# ── Ops Admin ──


def test_order_page_shows_timeline_and_packing_log(login: Callable[..., TestClient]) -> None:
    page = login("ops").get("/ops/orders/QB-48213").text
    assert "NOT PACKED" in page and "Coke 500ml" in page
    assert "Delivery timeline" in page


def test_cancel_preparing_order_triggers_auto_refund(login: Callable[..., TestClient], db: Database) -> None:
    login("ops").post("/ops/orders/QB-48301/cancel", data={"reason": "customer_request"})
    order = db.one("SELECT status, cancelled_by FROM ops_orders WHERE id = 'QB-48301'")
    assert order is not None and (order["status"], order["cancelled_by"]) == ("cancelled", "support")
    refund = db.one("SELECT * FROM pay_refunds WHERE order_id = 'QB-48301'")
    assert refund is not None and refund["created_by"] == "system:auto-refund"


def test_cannot_cancel_picked_up_order(login: Callable[..., TestClient], db: Database) -> None:
    response = login("ops").post("/ops/orders/QB-48298/cancel", data={"reason": "customer_request"})
    assert "already picked up" in response.text
    order = db.one("SELECT status FROM ops_orders WHERE id = 'QB-48298'")
    assert order is not None and order["status"] == "picked_up"


def test_raise_rider_incident(login: Callable[..., TestClient], db: Database) -> None:
    login("ops").post(
        "/ops/incidents",
        data={
            "target": "rider:RDR-204",
            "category": "rude_behaviour",
            "description": "Shouted at the customer and threw the bag.",
            "order_id": "QB-48230",
        },
    )
    incident = db.one("SELECT * FROM ops_incidents WHERE target_id = 'RDR-204'")
    assert incident is not None and incident["order_id"] == "QB-48230"


def test_customer_risk_panel_counts_recent_claims(login: Callable[..., TestClient]) -> None:
    page = login("ops").get("/ops/customers/CUST-1010").text
    assert "4 claim(s)" in page and "high claim frequency" in page


# ── Payments Console ──


def test_refund_is_two_step(login: Callable[..., TestClient], db: Database) -> None:
    client = login("payments")
    payment = db.one("SELECT id FROM pay_payments WHERE order_id = 'QB-48213'")
    assert payment is not None
    review = client.post(
        f"/payments/transactions/{payment['id']}/refund", data={"amount": "60", "reason_code": "missing_item"}
    )
    assert "Confirm refund" in review.text
    assert db.one("SELECT 1 FROM pay_refunds WHERE order_id = 'QB-48213'") is None  # nothing written yet
    done = client.post(
        f"/payments/transactions/{payment['id']}/refund/confirm",
        data={"amount": "6000", "reason_code": "missing_item", "note": "Coke not packed"},
    )
    assert "initiated" in done.text
    refund = db.one("SELECT amount, status FROM pay_refunds WHERE order_id = 'QB-48213'")
    assert refund is not None and (refund["amount"], refund["status"]) == (6000, "processing")
    paid = db.one("SELECT status FROM pay_payments WHERE id = ?", payment["id"])
    assert paid is not None and paid["status"] == "partially_refunded"


def test_refund_cannot_exceed_balance(login: Callable[..., TestClient], db: Database) -> None:
    payment = db.one("SELECT id, amount FROM pay_payments WHERE order_id = 'QB-48213'")
    assert payment is not None
    response = login("payments").post(
        f"/payments/transactions/{payment['id']}/refund/confirm",
        data={"amount": str(payment["amount"] + 1), "reason_code": "other"},
    )
    assert "exceeds refundable balance" in response.text


def test_issue_coupon(login: Callable[..., TestClient], db: Database) -> None:
    client = login("payments")
    response = client.post(
        "/payments/coupons",
        data={"customer_id": "cust-1002", "value": "50", "reason": "late_delivery", "order_id": "QB-48227"},
    )
    assert "worth ₹50.00 issued to CUST-1002" in response.text
    coupon = db.one("SELECT value FROM pay_coupons WHERE customer_id = 'CUST-1002'")
    assert coupon is not None and coupon["value"] == 5000


def test_coupon_value_is_capped(login: Callable[..., TestClient]) -> None:
    response = login("payments").post(
        "/payments/coupons", data={"customer_id": "CUST-1002", "value": "5000", "reason": "goodwill"}
    )
    assert "cannot exceed" in response.text
