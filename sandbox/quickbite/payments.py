"""Payments Console: captured payments, the refund ledger and goodwill coupons."""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from sandbox.quickbite.db import iso, next_id, now_iso, parse_rupees, rupees, utcnow
from sandbox.quickbite.web import current_user, login_router, redirect, render, sandbox_of

SYSTEM = "payments"
REFUND_REASONS = (
    "missing_item",
    "wrong_order",
    "food_quality",
    "late_delivery",
    "order_cancelled",
    "duplicate_charge",
    "goodwill",
    "other",
)
COUPON_REASONS = ("late_delivery", "food_quality", "service_recovery", "goodwill")
MAX_COUPON_PAISE = 100_000  # ₹1,000
REFUND_SETTLEMENT_DAYS = 5
# How long the gateway "hangs" before the 504 in the commit-then-timeout fault.
GATEWAY_TIMEOUT_SECONDS = 2.0

router = APIRouter(prefix=f"/{SYSTEM}")
router.include_router(login_router(SYSTEM))


def load_payment(conn: sqlite3.Connection, payment_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM pay_payments WHERE id = ?", (payment_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"Payment {payment_id} not found")
    return row


def refunded_total(conn: sqlite3.Connection, payment_id: str) -> int:
    total: int = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM pay_refunds WHERE payment_id = ?", (payment_id,)
    ).fetchone()[0]
    return total


@router.get("/")
def home() -> Response:
    return redirect(f"/{SYSTEM}/transactions")


@router.get("/transactions", response_class=HTMLResponse)
def transaction_list(request: Request, q: str = "") -> Response:
    user = current_user(request, SYSTEM)
    term = q.strip()
    if term:
        payments = sandbox_of(request).db.all(
            """SELECT * FROM pay_payments WHERE id LIKE ? OR order_id LIKE ? OR customer_id = ? OR gateway_ref LIKE ?
               ORDER BY captured_at DESC""",
            f"%{term}%",
            f"%{term}%",
            term.upper(),
            f"%{term}%",
        )
    else:
        payments = sandbox_of(request).db.all(
            "SELECT * FROM pay_payments ORDER BY captured_at DESC LIMIT 200"
        )
    return render(request, SYSTEM, "payments/transactions.html", user, payments=payments, q=q)


@router.get("/transactions/{payment_id}", response_class=HTMLResponse)
def transaction_detail(request: Request, payment_id: str) -> Response:
    user = current_user(request, SYSTEM)
    with sandbox_of(request).db.connect() as conn:
        payment = load_payment(conn, payment_id)
        refunds = conn.execute(
            "SELECT * FROM pay_refunds WHERE payment_id = ? ORDER BY created_at", (payment_id,)
        ).fetchall()
        refundable = payment["amount"] - refunded_total(conn, payment_id)
        same_order = conn.execute(
            "SELECT id, amount, status, captured_at FROM pay_payments WHERE order_id = ? AND id != ?",
            (payment["order_id"], payment_id),
        ).fetchall()
    return render(
        request,
        SYSTEM,
        "payments/transaction.html",
        user,
        payment=payment,
        refunds=refunds,
        refundable=refundable,
        same_order=same_order,
        reasons=REFUND_REASONS,
    )


@router.post("/transactions/{payment_id}/refund", response_class=HTMLResponse)
def refund_review(
    request: Request, payment_id: str, amount: str = Form(), reason_code: str = Form(), note: str = Form("")
) -> Response:
    """Step 1 of 2: validate the refund and show a confirmation page. Nothing is written yet."""
    user = current_user(request, SYSTEM)
    back = f"/{SYSTEM}/transactions/{payment_id}"
    if reason_code not in REFUND_REASONS:
        return redirect(back, error="Select a refund reason.")
    try:
        paise = parse_rupees(amount)
    except ValueError:
        return redirect(back, error="Enter a valid refund amount.")
    with sandbox_of(request).db.connect() as conn:
        payment = load_payment(conn, payment_id)
        refundable = payment["amount"] - refunded_total(conn, payment_id)
    if paise > refundable:
        return redirect(back, error=f"Refund exceeds refundable balance of {rupees(refundable)}.")
    return render(
        request,
        SYSTEM,
        "payments/refund_confirm.html",
        user,
        payment=payment,
        amount=paise,
        reason_code=reason_code,
        note=note.strip(),
        refundable=refundable,
    )


@router.post("/transactions/{payment_id}/refund/confirm")
async def refund_confirm(
    request: Request, payment_id: str, amount: int = Form(), reason_code: str = Form(), note: str = Form("")
) -> Response:
    """Step 2 of 2: write the refund to the ledger."""
    user = current_user(request, SYSTEM)
    back = f"/{SYSTEM}/transactions/{payment_id}"
    if reason_code not in REFUND_REASONS or amount <= 0:
        return redirect(back, error="Invalid refund request.")
    sandbox = sandbox_of(request)
    now = now_iso()
    with sandbox.db.connect() as conn:
        payment = load_payment(conn, payment_id)
        already = refunded_total(conn, payment_id)
        if amount > payment["amount"] - already:
            return redirect(
                back, error=f"Refund exceeds refundable balance of {rupees(payment['amount'] - already)}."
            )
        refund_id = next_id(conn, "pay_refunds", "RF-", 70001)
        conn.execute(
            """INSERT INTO pay_refunds (id, payment_id, order_id, customer_id, amount, reason_code, note,
                 status, created_by, created_at, expected_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?)""",
            (
                refund_id,
                payment_id,
                payment["order_id"],
                payment["customer_id"],
                amount,
                reason_code,
                note.strip(),
                user["display_name"],
                now,
                iso(utcnow() + timedelta(days=REFUND_SETTLEMENT_DAYS)),
            ),
        )
        status = "refunded" if already + amount >= payment["amount"] else "partially_refunded"
        conn.execute("UPDATE pay_payments SET status = ? WHERE id = ?", (status, payment_id))

    # Fault: the refund is committed, but the gateway never answers the browser.
    if sandbox.faults.take("refund_commit_timeout_next", SYSTEM, request.url.path):
        await asyncio.sleep(GATEWAY_TIMEOUT_SECONDS)
        return HTMLResponse(GATEWAY_TIMEOUT_PAGE, status_code=504)
    return redirect(
        f"/{SYSTEM}/refunds/{refund_id}", msg=f"Refund {refund_id} of {rupees(amount)} initiated."
    )


@router.get("/refunds", response_class=HTMLResponse)
def refund_list(request: Request, q: str = "") -> Response:
    user = current_user(request, SYSTEM)
    term = q.strip()
    if term:
        refunds = sandbox_of(request).db.all(
            """SELECT * FROM pay_refunds WHERE id LIKE ? OR order_id LIKE ? OR payment_id LIKE ? OR customer_id = ?
               ORDER BY created_at DESC""",
            f"%{term}%",
            f"%{term}%",
            f"%{term}%",
            term.upper(),
        )
    else:
        refunds = sandbox_of(request).db.all("SELECT * FROM pay_refunds ORDER BY created_at DESC LIMIT 200")
    return render(request, SYSTEM, "payments/refunds.html", user, refunds=refunds, q=q)


@router.get("/refunds/{refund_id}", response_class=HTMLResponse)
def refund_detail(request: Request, refund_id: str) -> Response:
    user = current_user(request, SYSTEM)
    refund = sandbox_of(request).db.one("SELECT * FROM pay_refunds WHERE id = ?", refund_id)
    if refund is None:
        raise HTTPException(404, f"Refund {refund_id} not found")
    return render(request, SYSTEM, "payments/refund.html", user, refund=refund)


@router.get("/coupons", response_class=HTMLResponse)
def coupon_list(request: Request, q: str = "") -> Response:
    user = current_user(request, SYSTEM)
    term = q.strip()
    db = sandbox_of(request).db
    if term:
        coupons = db.all(
            "SELECT * FROM pay_coupons WHERE code LIKE ? OR customer_id = ? OR order_id LIKE ? "
            "ORDER BY created_at DESC",
            f"%{term}%",
            term.upper(),
            f"%{term}%",
        )
    else:
        coupons = db.all("SELECT * FROM pay_coupons ORDER BY created_at DESC LIMIT 200")
    return render(
        request,
        SYSTEM,
        "payments/coupons.html",
        user,
        coupons=coupons,
        q=q,
        reasons=COUPON_REASONS,
        max_value=MAX_COUPON_PAISE,
    )


@router.post("/coupons")
def issue_coupon(
    request: Request,
    customer_id: str = Form(),
    value: str = Form(),
    reason: str = Form(),
    order_id: str = Form(""),
    validity_days: int = Form(30),
) -> Response:
    user = current_user(request, SYSTEM)
    back = f"/{SYSTEM}/coupons"
    customer_id = customer_id.strip().upper()
    if reason not in COUPON_REASONS:
        return redirect(back, error="Select a coupon reason.")
    try:
        paise = parse_rupees(value)
    except ValueError:
        return redirect(back, error="Enter a valid coupon value.")
    if paise > MAX_COUPON_PAISE:
        return redirect(back, error=f"Coupon value cannot exceed {rupees(MAX_COUPON_PAISE)}.")
    if not 1 <= validity_days <= 90:
        return redirect(back, error="Validity must be between 1 and 90 days.")
    with sandbox_of(request).db.connect() as conn:
        if (
            conn.execute("SELECT 1 FROM pay_payments WHERE customer_id = ?", (customer_id,)).fetchone()
            is None
        ):
            return redirect(back, error=f"No payment history for customer {customer_id}.")
        code = f"QBSORRY-{secrets.token_hex(3).upper()}"
        conn.execute(
            "INSERT INTO pay_coupons (code, customer_id, value, reason, order_id, created_by, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                code,
                customer_id,
                paise,
                reason,
                order_id.strip().upper() or None,
                user["display_name"],
                now_iso(),
                iso(utcnow() + timedelta(days=validity_days)),
            ),
        )
    return redirect(f"{back}?q={code}", msg=f"Coupon {code} worth {rupees(paise)} issued to {customer_id}.")


GATEWAY_TIMEOUT_PAGE = """<!doctype html><html lang="en"><head><title>504 Gateway Timeout</title></head>
<body><main><h1>504 Gateway Timeout</h1>
<p>The payment gateway did not respond in time. The status of your request is unknown.</p>
</main></body></html>"""
