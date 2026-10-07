"""Ops Admin: orders, delivery timelines, restaurants, riders and incidents."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from sandbox.quickbite.db import ago, iso, next_id, now_iso, utcnow
from sandbox.quickbite.web import current_user, login_router, redirect, render, sandbox_of

SYSTEM = "ops"
CANCELLABLE = ("placed", "accepted", "preparing")
CANCEL_REASONS = (
    "customer_request",
    "restaurant_unavailable",
    "item_out_of_stock",
    "address_issue",
    "duplicate_order",
)
INCIDENT_CATEGORIES = {
    "restaurant": ("missing_item", "wrong_order", "food_quality", "packaging", "delay", "other"),
    "rider": ("rude_behaviour", "late_delivery", "order_tampering", "unsafe_driving", "other"),
}
# Refund reason codes that count as a customer "claim" for the risk panel.
CLAIM_REASONS = ("missing_item", "wrong_order", "food_quality", "late_delivery")

router = APIRouter(prefix=f"/{SYSTEM}")
router.include_router(login_router(SYSTEM))


def load(conn: sqlite3.Connection, table: str, key: str, label: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (key,)).fetchone()
    if row is None:
        raise HTTPException(404, f"{label} {key} not found")
    return row


@router.get("/")
def home() -> Response:
    return redirect(f"/{SYSTEM}/orders")


@router.get("/orders", response_class=HTMLResponse)
def order_list(request: Request, q: str = "", status: str = "") -> Response:
    user = current_user(request, SYSTEM)
    clauses: list[str] = []
    params: list[str] = []
    if q.strip():
        clauses.append(
            "(o.id LIKE ? OR c.name LIKE ? OR c.email LIKE ? OR c.phone LIKE ? OR r.name LIKE ? OR c.id = ?)"
        )
        params.extend([f"%{q.strip()}%"] * 5 + [q.strip().upper()])
    if status:
        clauses.append("o.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    orders = sandbox_of(request).db.all(
        f"""SELECT o.*, c.name AS customer_name, r.name AS restaurant_name
            FROM ops_orders o JOIN ops_customers c ON c.id = o.customer_id
            JOIN ops_restaurants r ON r.id = o.restaurant_id {where}
            ORDER BY o.placed_at DESC LIMIT 200""",
        *params,
    )
    return render(request, SYSTEM, "ops/orders.html", user, orders=orders, q=q, status=status)


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail(request: Request, order_id: str) -> Response:
    user = current_user(request, SYSTEM)
    with sandbox_of(request).db.connect() as conn:
        order = load(conn, "ops_orders", order_id, "Order")
        context = {
            "order": order,
            "customer": load(conn, "ops_customers", order["customer_id"], "Customer"),
            "restaurant": load(conn, "ops_restaurants", order["restaurant_id"], "Restaurant"),
            "rider": conn.execute("SELECT * FROM ops_riders WHERE id = ?", (order["rider_id"],)).fetchone(),
            "items": conn.execute(
                "SELECT * FROM ops_order_items WHERE order_id = ? ORDER BY line_no", (order_id,)
            ).fetchall(),
            "events": conn.execute(
                "SELECT * FROM ops_order_events WHERE order_id = ? ORDER BY at, id", (order_id,)
            ).fetchall(),
            "packing": conn.execute(
                "SELECT * FROM ops_packing_log WHERE order_id = ? ORDER BY line_no", (order_id,)
            ).fetchall(),
            "incidents": conn.execute(
                "SELECT * FROM ops_incidents WHERE order_id = ? ORDER BY created_at", (order_id,)
            ).fetchall(),
        }
    return render(
        request,
        SYSTEM,
        "ops/order.html",
        user,
        cancellable=order["status"] in CANCELLABLE,
        cancel_reasons=CANCEL_REASONS,
        incident_categories=INCIDENT_CATEGORIES,
        **context,
    )


@router.post("/orders/{order_id}/cancel")
def cancel_order(request: Request, order_id: str, reason: str = Form(), note: str = Form("")) -> Response:
    user = current_user(request, SYSTEM)
    if reason not in CANCEL_REASONS:
        return redirect(f"/{SYSTEM}/orders/{order_id}", error="Select a cancellation reason.")
    now = now_iso()
    with sandbox_of(request).db.connect() as conn:
        order = load(conn, "ops_orders", order_id, "Order")
        if order["status"] not in CANCELLABLE:
            return redirect(
                f"/{SYSTEM}/orders/{order_id}",
                error=f"Order cannot be cancelled: it is already {order['status'].replace('_', ' ')}.",
            )
        conn.execute(
            "UPDATE ops_orders SET status = 'cancelled', cancelled_at = ?, cancelled_by = 'support', "
            "cancel_reason = ? WHERE id = ?",
            (now, reason, order_id),
        )
        conn.execute(
            "INSERT INTO ops_order_events (order_id, at, event, note) VALUES (?, ?, 'cancelled', ?)",
            (order_id, now, f"Cancelled by {user['display_name']} ({reason}). {note}".strip()),
        )
        # Cancelling a paid order publishes an event the Payments system acts on by
        # starting an automatic refund. Modelled here as a direct write.
        payment = conn.execute(
            "SELECT * FROM pay_payments WHERE order_id = ? AND status != 'refunded' ORDER BY captured_at LIMIT 1",
            (order_id,),
        ).fetchone()
        if payment:
            refund_id = next_id(conn, "pay_refunds", "RF-", 70001)
            conn.execute(
                """INSERT INTO pay_refunds (id, payment_id, order_id, customer_id, amount, reason_code, note,
                     status, created_by, created_at, expected_by)
                   VALUES (?, ?, ?, ?, ?, 'order_cancelled', 'Automatic refund on order cancellation',
                     'processing', 'system:auto-refund', ?, ?)""",
                (
                    refund_id,
                    payment["id"],
                    order_id,
                    payment["customer_id"],
                    payment["amount"],
                    now,
                    iso(utcnow() + timedelta(days=5)),
                ),
            )
            conn.execute("UPDATE pay_payments SET status = 'refunded' WHERE id = ?", (payment["id"],))
    return redirect(
        f"/{SYSTEM}/orders/{order_id}", msg="Order cancelled. Any payment will be refunded automatically."
    )


@router.post("/incidents")
def create_incident(
    request: Request,
    target: str = Form(),
    category: str = Form(),
    description: str = Form(),
    order_id: str = Form(""),
) -> Response:
    user = current_user(request, SYSTEM)
    back = f"/{SYSTEM}/orders/{order_id}" if order_id else f"/{SYSTEM}/incidents"
    target_type, _, target_id = target.partition(":")
    if target_type not in INCIDENT_CATEGORIES or category not in INCIDENT_CATEGORIES[target_type]:
        return redirect(back, error="Select a valid incident type and category.")
    if len(description.strip()) < 10:
        return redirect(back, error="Describe the incident (at least 10 characters).")
    with sandbox_of(request).db.connect() as conn:
        table = "ops_restaurants" if target_type == "restaurant" else "ops_riders"
        load(conn, table, target_id, target_type.title())
        incident_id = next_id(conn, "ops_incidents", "INC-", 5001)
        conn.execute(
            "INSERT INTO ops_incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                incident_id,
                target_type,
                target_id,
                order_id or None,
                category,
                description.strip(),
                user["display_name"],
                now_iso(),
            ),
        )
    return redirect(back, msg=f"Incident {incident_id} raised against {target_type} {target_id}.")


@router.get("/incidents", response_class=HTMLResponse)
def incident_list(request: Request) -> Response:
    user = current_user(request, SYSTEM)
    incidents = sandbox_of(request).db.all("SELECT * FROM ops_incidents ORDER BY created_at DESC")
    return render(request, SYSTEM, "ops/incidents.html", user, incidents=incidents)


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def customer_detail(request: Request, customer_id: str) -> Response:
    user = current_user(request, SYSTEM)
    with sandbox_of(request).db.connect() as conn:
        customer = load(conn, "ops_customers", customer_id, "Customer")
        orders = conn.execute(
            """SELECT o.*, r.name AS restaurant_name FROM ops_orders o
               JOIN ops_restaurants r ON r.id = o.restaurant_id
               WHERE o.customer_id = ? ORDER BY o.placed_at DESC""",
            (customer_id,),
        ).fetchall()
        # Risk panel: refund claims in the last 30 days, fed from the Payments ledger.
        claims = conn.execute(
            f"""SELECT id, order_id, amount, reason_code, created_at FROM pay_refunds
                WHERE customer_id = ? AND created_at >= ?
                AND reason_code IN ({",".join("?" * len(CLAIM_REASONS))})
                ORDER BY created_at DESC""",
            (customer_id, ago(days=30), *CLAIM_REASONS),
        ).fetchall()
    return render(request, SYSTEM, "ops/customer.html", user, customer=customer, orders=orders, claims=claims)


@router.get("/restaurants/{restaurant_id}", response_class=HTMLResponse)
def restaurant_detail(request: Request, restaurant_id: str) -> Response:
    user = current_user(request, SYSTEM)
    with sandbox_of(request).db.connect() as conn:
        restaurant = load(conn, "ops_restaurants", restaurant_id, "Restaurant")
        incidents = conn.execute(
            "SELECT * FROM ops_incidents WHERE target_type = 'restaurant' AND target_id = ? "
            "ORDER BY created_at DESC",
            (restaurant_id,),
        ).fetchall()
        orders = conn.execute(
            """SELECT o.*, c.name AS customer_name FROM ops_orders o
               JOIN ops_customers c ON c.id = o.customer_id
               WHERE o.restaurant_id = ? ORDER BY o.placed_at DESC LIMIT 100""",
            (restaurant_id,),
        ).fetchall()
    return render(
        request,
        SYSTEM,
        "ops/restaurant.html",
        user,
        restaurant=restaurant,
        incidents=incidents,
        orders=orders,
    )


@router.post("/restaurants/{restaurant_id}/flag")
def flag_restaurant(request: Request, restaurant_id: str, flagged: str = Form("1")) -> Response:
    current_user(request, SYSTEM)
    with sandbox_of(request).db.connect() as conn:
        load(conn, "ops_restaurants", restaurant_id, "Restaurant")
        conn.execute(
            "UPDATE ops_restaurants SET flagged = ? WHERE id = ?", (int(flagged == "1"), restaurant_id)
        )
    msg = "Restaurant flagged for quality review." if flagged == "1" else "Quality-review flag removed."
    return redirect(f"/{SYSTEM}/restaurants/{restaurant_id}", msg=msg)


@router.get("/riders/{rider_id}", response_class=HTMLResponse)
def rider_detail(request: Request, rider_id: str) -> Response:
    user = current_user(request, SYSTEM)
    with sandbox_of(request).db.connect() as conn:
        rider = load(conn, "ops_riders", rider_id, "Rider")
        incidents = conn.execute(
            "SELECT * FROM ops_incidents WHERE target_type = 'rider' AND target_id = ? ORDER BY created_at DESC",
            (rider_id,),
        ).fetchall()
        orders = conn.execute(
            "SELECT * FROM ops_orders WHERE rider_id = ? ORDER BY placed_at DESC LIMIT 50", (rider_id,)
        ).fetchall()
    return render(request, SYSTEM, "ops/rider.html", user, rider=rider, incidents=incidents, orders=orders)
