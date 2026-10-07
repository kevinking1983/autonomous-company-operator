"""Support Desk: the ticketing tool where customer complaints arrive and get answered."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from sandbox.quickbite.db import iso, now_iso, utcnow
from sandbox.quickbite.web import current_user, login_router, redirect, render, sandbox_of

SYSTEM = "support"
STATUSES = ("open", "pending_customer", "on_hold", "resolved", "closed")
ACTIVE_STATUSES = ("open", "pending_customer", "on_hold")
CATEGORIES = (
    "missing_item",
    "late_delivery",
    "wrong_order",
    "food_quality",
    "payment_issue",
    "refund_status",
    "order_change",
    "rider_behaviour",
    "other",
)

router = APIRouter(prefix=f"/{SYSTEM}")
router.include_router(login_router(SYSTEM))


def deliver_due_replies(conn: sqlite3.Connection) -> None:
    """Scripted customers answer once their reply is due (see support_scripted_replies)."""
    now = now_iso()
    due = conn.execute(
        "SELECT ticket_id, body FROM support_scripted_replies WHERE delivered = 0 AND due_at <= ?", (now,)
    ).fetchall()
    for row in due:
        ticket = conn.execute(
            "SELECT requester_name FROM support_tickets WHERE id = ?", (row["ticket_id"],)
        ).fetchone()
        conn.execute(
            "INSERT INTO support_messages (ticket_id, author_type, author, body, at) "
            "VALUES (?, 'customer', ?, ?, ?)",
            (row["ticket_id"], ticket["requester_name"], row["body"], now),
        )
        conn.execute(
            "UPDATE support_tickets SET status = 'open', updated_at = ? WHERE id = ?", (now, row["ticket_id"])
        )
        conn.execute(
            "UPDATE support_scripted_replies SET delivered = 1 WHERE ticket_id = ?", (row["ticket_id"],)
        )


def load_ticket(conn: sqlite3.Connection, ticket_id: str) -> sqlite3.Row:
    ticket = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        raise HTTPException(404, f"Ticket {ticket_id} not found")
    row: sqlite3.Row = ticket
    return row


@router.get("/")
def home() -> Response:
    return redirect(f"/{SYSTEM}/tickets")


@router.get("/tickets", response_class=HTMLResponse)
def ticket_list(request: Request, status: str = "active", q: str = "", category: str = "") -> Response:
    user = current_user(request, SYSTEM)
    clauses: list[str] = []
    params: list[str] = []
    if status == "active":
        clauses.append(f"status IN ({','.join('?' * len(ACTIVE_STATUSES))})")
        params.extend(ACTIVE_STATUSES)
    elif status in STATUSES:
        clauses.append("status = ?")
        params.append(status)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if q.strip():
        clauses.append(
            "(id LIKE ? OR subject LIKE ? OR requester_email LIKE ? OR requester_name LIKE ? OR order_id LIKE ?)"
        )
        params.extend([f"%{q.strip()}%"] * 5)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with sandbox_of(request).db.connect() as conn:
        deliver_due_replies(conn)
        tickets = conn.execute(
            f"SELECT * FROM support_tickets {where} ORDER BY created_at DESC", params
        ).fetchall()
    return render(
        request,
        SYSTEM,
        "support/tickets.html",
        user,
        tickets=tickets,
        status=status,
        q=q,
        category=category,
        statuses=STATUSES,
        categories=CATEGORIES,
    )


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(request: Request, ticket_id: str) -> Response:
    user = current_user(request, SYSTEM)
    with sandbox_of(request).db.connect() as conn:
        deliver_due_replies(conn)
        ticket = load_ticket(conn, ticket_id)
        messages = conn.execute(
            "SELECT * FROM support_messages WHERE ticket_id = ? ORDER BY at, id", (ticket_id,)
        ).fetchall()
        attachments = conn.execute(
            "SELECT id, filename, content_type, uploaded_at FROM support_attachments WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchall()
        other_tickets = conn.execute(
            "SELECT id, subject, status, category, order_id, created_at FROM support_tickets "
            "WHERE customer_id = ? AND id != ? ORDER BY created_at DESC",
            (ticket["customer_id"], ticket_id),
        ).fetchall()
    return render(
        request,
        SYSTEM,
        "support/ticket.html",
        user,
        ticket=ticket,
        messages=messages,
        attachments=attachments,
        other_tickets=other_tickets,
        statuses=STATUSES,
        categories=CATEGORIES,
    )


@router.post("/tickets/{ticket_id}/reply")
def reply(request: Request, ticket_id: str, body: str = Form(), set_status: str = Form("")) -> Response:
    user = current_user(request, SYSTEM)
    if not body.strip():
        return redirect(f"/{SYSTEM}/tickets/{ticket_id}", error="Reply cannot be empty.")
    if set_status and set_status not in STATUSES:
        return redirect(f"/{SYSTEM}/tickets/{ticket_id}", error="Unknown status.")
    now = now_iso()
    with sandbox_of(request).db.connect() as conn:
        load_ticket(conn, ticket_id)
        conn.execute(
            "INSERT INTO support_messages (ticket_id, author_type, author, body, at) VALUES (?, 'agent', ?, ?, ?)",
            (ticket_id, user["display_name"], body.strip(), now),
        )
        conn.execute(
            "UPDATE support_tickets SET updated_at = ?, status = COALESCE(NULLIF(?, ''), status) WHERE id = ?",
            (now, set_status, ticket_id),
        )
        script = conn.execute(
            "SELECT delay_seconds FROM support_scripted_replies WHERE ticket_id = ? AND due_at IS NULL",
            (ticket_id,),
        ).fetchone()
        if script:
            due = iso(utcnow() + timedelta(seconds=script["delay_seconds"]))
            conn.execute(
                "UPDATE support_scripted_replies SET due_at = ? WHERE ticket_id = ?", (due, ticket_id)
            )
    return redirect(f"/{SYSTEM}/tickets/{ticket_id}", msg="Reply sent to customer.")


@router.post("/tickets/{ticket_id}/note")
def add_note(request: Request, ticket_id: str, body: str = Form()) -> Response:
    user = current_user(request, SYSTEM)
    if not body.strip():
        return redirect(f"/{SYSTEM}/tickets/{ticket_id}", error="Note cannot be empty.")
    now = now_iso()
    with sandbox_of(request).db.connect() as conn:
        load_ticket(conn, ticket_id)
        conn.execute(
            "INSERT INTO support_messages (ticket_id, author_type, author, body, internal, at) "
            "VALUES (?, 'agent', ?, ?, 1, ?)",
            (ticket_id, user["display_name"], body.strip(), now),
        )
        conn.execute("UPDATE support_tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
    return redirect(f"/{SYSTEM}/tickets/{ticket_id}", msg="Internal note added.")


@router.post("/tickets/{ticket_id}/update")
def update_ticket(
    request: Request,
    ticket_id: str,
    status: str = Form(""),
    category: str = Form(""),
    linked_ticket_id: str = Form(""),
) -> Response:
    current_user(request, SYSTEM)
    if status and status not in STATUSES:
        return redirect(f"/{SYSTEM}/tickets/{ticket_id}", error="Unknown status.")
    if category and category not in CATEGORIES:
        return redirect(f"/{SYSTEM}/tickets/{ticket_id}", error="Unknown category.")
    linked = linked_ticket_id.strip().upper()
    with sandbox_of(request).db.connect() as conn:
        load_ticket(conn, ticket_id)
        if linked:
            if linked == ticket_id:
                return redirect(
                    f"/{SYSTEM}/tickets/{ticket_id}", error="A ticket cannot be linked to itself."
                )
            if conn.execute("SELECT 1 FROM support_tickets WHERE id = ?", (linked,)).fetchone() is None:
                return redirect(f"/{SYSTEM}/tickets/{ticket_id}", error=f"Ticket {linked} does not exist.")
        conn.execute(
            """UPDATE support_tickets SET
                 status = COALESCE(NULLIF(?, ''), status),
                 category = COALESCE(NULLIF(?, ''), category),
                 linked_ticket_id = COALESCE(NULLIF(?, ''), linked_ticket_id),
                 updated_at = ?
               WHERE id = ?""",
            (status, category, linked, now_iso(), ticket_id),
        )
    return redirect(f"/{SYSTEM}/tickets/{ticket_id}", msg="Ticket updated.")


@router.get("/attachments/{attachment_id}")
def attachment(request: Request, attachment_id: int) -> Response:
    current_user(request, SYSTEM)
    row = sandbox_of(request).db.one(
        "SELECT filename, content_type, data FROM support_attachments WHERE id = ?", attachment_id
    )
    if row is None:
        raise HTTPException(404, "Attachment not found")
    return Response(
        row["data"],
        media_type=row["content_type"],
        headers={"Content-Disposition": f'inline; filename="{row["filename"]}"'},
    )
