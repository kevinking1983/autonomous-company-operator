"""Every scenario in the catalogue has the data the operator needs to resolve it."""

import pytest

from sandbox.quickbite.db import Database, ago, minutes_between
from sandbox.quickbite.seed import (
    KITCHEN_FIRE_ORDERS,
    LATE_DELIVERY_BATCH,
    SCENARIOS,
    Scenario,
)


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=lambda s: s.key)
def test_scenario_ticket_and_order_exist(db: Database, scenario: Scenario) -> None:
    ticket = db.one("SELECT * FROM support_tickets WHERE id = ?", scenario.ticket_id)
    assert ticket is not None and ticket["status"] == "open"
    assert ticket["order_id"] == scenario.order_id
    assert db.one("SELECT 1 FROM ops_orders WHERE id = ?", scenario.order_id) is not None
    assert db.one("SELECT 1 FROM pay_payments WHERE order_id = ?", scenario.order_id) is not None


def test_missing_item_is_visible_in_packing_log(db: Database) -> None:
    row = db.one(
        "SELECT packed FROM ops_packing_log WHERE order_id = 'QB-48213' AND item_name = 'Coke 500ml'"
    )
    assert row is not None and row["packed"] == 0


def test_late_delivery_is_47_minutes_not_90(db: Database) -> None:
    order = db.one("SELECT promised_at, delivered_at FROM ops_orders WHERE id = 'QB-48227'")
    assert order is not None
    assert minutes_between(order["promised_at"], order["delivered_at"]) == 47


def test_late_delivery_batch_has_varied_delays(db: Database) -> None:
    delays = []
    for ticket_id in LATE_DELIVERY_BATCH:
        order = db.one(
            "SELECT o.promised_at, o.delivered_at FROM support_tickets t JOIN ops_orders o ON o.id = t.order_id "
            "WHERE t.id = ?",
            ticket_id,
        )
        assert order is not None
        delays.append(minutes_between(order["promised_at"], order["delivered_at"]))
    assert sorted(delays) == [12, 35, 47, 72]


def test_wrong_order_photo_points_at_a_real_order(db: Database) -> None:
    assert db.one("SELECT 1 FROM support_attachments WHERE ticket_id = 'TKT-1003'") is not None
    swapped = db.one("SELECT rider_id FROM ops_orders WHERE id = 'QB-48244'")
    ordered = db.one("SELECT rider_id, total FROM ops_orders WHERE id = 'QB-48241'")
    assert swapped is not None and ordered is not None
    assert swapped["rider_id"] == ordered["rider_id"]
    assert ordered["total"] > 50000  # a full refund crosses the ₹500 approval threshold


def test_cancelled_but_charged_has_no_refund(db: Database) -> None:
    order = db.one("SELECT status, cancelled_by FROM ops_orders WHERE id = 'QB-48260'")
    assert order is not None and (order["status"], order["cancelled_by"]) == ("cancelled", "restaurant")
    assert db.one("SELECT 1 FROM pay_refunds WHERE order_id = 'QB-48260'") is None


def test_cancelled_already_refunded_has_auto_refund(db: Database) -> None:
    refund = db.one("SELECT created_by, status FROM pay_refunds WHERE order_id = 'QB-48263'")
    assert refund is not None and (refund["created_by"], refund["status"]) == (
        "system:auto-refund",
        "processing",
    )


def test_double_charge_has_two_identical_captures(db: Database) -> None:
    payments = db.all("SELECT amount, gateway_ref FROM pay_payments WHERE order_id = 'QB-48270'")
    assert len(payments) == 2
    assert payments[0]["amount"] == payments[1]["amount"]
    assert payments[0]["gateway_ref"] != payments[1]["gateway_ref"]


def test_refund_status_enquiry_has_refund_in_flight(db: Database) -> None:
    refund = db.one("SELECT status, amount FROM pay_refunds WHERE order_id = 'QB-48140'")
    assert refund is not None and (refund["status"], refund["amount"]) == ("processing", 15000)


def test_cancel_scenarios_differ_by_order_state(db: Database) -> None:
    states = {
        r["id"]: r["status"]
        for r in db.all("SELECT id, status FROM ops_orders WHERE id IN ('QB-48301', 'QB-48298')")
    }
    assert states == {"QB-48301": "preparing", "QB-48298": "picked_up"}


def test_repeat_claimant_history(db: Database) -> None:
    claims = db.all(
        "SELECT 1 FROM pay_refunds WHERE customer_id = 'CUST-1010' AND reason_code = 'missing_item' AND created_at >= ?",
        ago(days=30),
    )
    assert len(claims) == 4
    unpacked = db.one("SELECT COUNT(*) AS n FROM ops_packing_log WHERE order_id = 'QB-48275' AND packed = 0")
    assert unpacked is not None and unpacked["n"] == 0  # the restaurant says everything was packed


def test_duplicate_ticket_was_already_refunded(db: Database) -> None:
    earlier = db.one("SELECT status FROM support_tickets WHERE id = 'TKT-0995'")
    assert earlier is not None and earlier["status"] == "resolved"
    assert db.one("SELECT 1 FROM pay_refunds WHERE order_id = 'QB-48190'") is not None


def test_vague_ticket_has_scripted_customer(db: Database) -> None:
    assert db.one("SELECT 1 FROM support_scripted_replies WHERE ticket_id = 'TKT-1012'") is not None


def test_kitchen_fire_orders_were_cancelled_and_auto_refunded(db: Database) -> None:
    for order_id in KITCHEN_FIRE_ORDERS:
        order = db.one("SELECT status, cancel_reason FROM ops_orders WHERE id = ?", order_id)
        assert order is not None and (order["status"], order["cancel_reason"]) == (
            "cancelled",
            "restaurant_unavailable",
        )
        assert db.one("SELECT 1 FROM pay_refunds WHERE order_id = ?", order_id) is not None
