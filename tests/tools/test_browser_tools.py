"""The operator's tools driving a real browser against the live QuickBite sandbox."""

import pytest

from company_operator.tools import ToolContext, ToolRegistry
from tests.conftest import LiveSandbox
from tests.tools.helpers import Driver


@pytest.fixture
def op(registry: ToolRegistry, ctx: ToolContext) -> Driver:
    return Driver(registry, ctx)


def payment_id(sandbox: LiveSandbox, order_id: str) -> str:
    return str(next(p["id"] for p in sandbox.rows("pay_payments") if p["order_id"] == order_id))


# ── perception and sessions ──


async def test_open_signs_in_and_snapshots_the_page(op: Driver, ctx: ToolContext) -> None:
    result = await op.open("support", "tickets/TKT-1003")
    assert result.ok, result.output
    assert "URL: " in result.output and "/support/tickets/TKT-1003" in result.output
    assert "# TKT-1003: Received someone else's order" in result.output
    assert 'textarea "Reply to customer"' in result.output
    assert 'combobox "Status"' in result.output and "options=[open, pending_customer" in result.output
    assert 'image "Customer attachment IMG_20261007_bag.png"' in result.output
    assert [e.data["system"] for e in ctx.log.of_type("browser.login")] == ["support"]


async def test_tables_are_rendered_with_row_refs(op: Driver) -> None:
    result = await op.open("support", "tickets")
    assert 'table "Tickets"' in result.output
    assert any(line.strip().startswith("| TKT-1001 [e") for line in result.output.splitlines())


async def test_stale_ref_is_reported_not_crashed(op: Driver) -> None:
    await op.open("support", "tickets")
    result = await op.call("browser_click", ref="e9999")
    assert result.error == "not_found"


async def test_cannot_leave_the_company_systems(op: Driver) -> None:
    result = await op.call("browser_open", path="https://example.com/")
    assert result.error == "policy"


# ── safe retries ──


async def test_failed_reads_are_retried_automatically(
    op: Driver, sandbox: LiveSandbox, ctx: ToolContext
) -> None:
    await op.open("support", "tickets")
    sandbox.faults(fail_next=2)
    result = await op.open("support", "tickets/TKT-1001")
    assert result.ok, result.output
    assert "Recovered after 2 automatic retries" in result.output
    assert len(ctx.log.of_type("browser.retry")) == 2


async def test_persistent_failure_is_transient_error(op: Driver, sandbox: LiveSandbox) -> None:
    await op.open("ops", "orders")
    sandbox.faults(error_rate=1.0, systems=["ops"])
    result = await op.open("ops", "orders/QB-48213")
    assert result.error == "transient"


async def test_session_expiry_on_read_is_transparent(op: Driver, sandbox: LiveSandbox) -> None:
    await op.open("ops", "orders")
    sandbox.faults(session_expiry_in=1)
    result = await op.open("ops", "orders/QB-48213")
    assert result.ok and "Signed in to ops." in result.output


# ── declaring and enforcing actions ──


async def test_undeclared_change_is_blocked(op: Driver, sandbox: LiveSandbox) -> None:
    await op.open("support", "tickets/TKT-1001")
    await op.fill('textarea "Internal note', "checking packing log")
    result = await op.click('button "Add internal note"')
    assert result.error == "policy"
    assert "was not authorised" in result.output
    assert not [m for m in sandbox.rows("support_messages") if m["body"] == "checking packing log"]


async def test_declared_change_goes_through(op: Driver, sandbox: LiveSandbox) -> None:
    await op.open("support", "tickets/TKT-1001")
    await op.fill('textarea "Internal note', "checking packing log")
    result = await op.click('button "Add internal note"', "support.add_internal_note", ticket_id="TKT-1001")
    assert result.ok, result.output
    assert [m for m in sandbox.rows("support_messages") if m["body"] == "checking packing log"]


async def refund_form(op: Driver, sandbox: LiveSandbox, order_id: str, amount: str, reason: str) -> str:
    pay = payment_id(sandbox, order_id)
    await op.open("payments", f"transactions/{pay}")
    await op.fill('textbox "Refund amount', amount)
    await op.select('combobox "Reason"', reason)
    review = await op.click('button "Review refund"')  # a POST that only renders a confirmation page
    assert review.ok, review.output
    assert "# Confirm refund" in review.output
    return pay


async def test_refund_within_policy(op: Driver, sandbox: LiveSandbox) -> None:
    pay = await refund_form(op, sandbox, "QB-48213", "60", "missing_item")
    result = await op.click(
        'button "Confirm refund"', "payments.refund", payment_id=pay, amount=60, reason="missing_item"
    )
    assert result.ok, result.output
    refunds = [r for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48213"]
    assert [(r["amount"], r["reason_code"]) for r in refunds] == [(6000, "missing_item")]


async def test_submitted_amount_must_match_declaration(op: Driver, sandbox: LiveSandbox) -> None:
    pay = await refund_form(op, sandbox, "QB-48213", "500", "missing_item")
    result = await op.click(
        'button "Confirm refund"', "payments.refund", payment_id=pay, amount=60, reason="missing_item"
    )
    assert result.error == "policy"
    assert "amount is '50000', authorised amount=60" in result.output
    assert not [r for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48213"]


async def test_grant_is_single_use(op: Driver, sandbox: LiveSandbox) -> None:
    pay = await refund_form(op, sandbox, "QB-48213", "60", "missing_item")
    await op.click(
        'button "Confirm refund"', "payments.refund", payment_id=pay, amount=60, reason="missing_item"
    )
    await refund_form(op, sandbox, "QB-48213", "60", "missing_item")
    replay = await op.click('button "Confirm refund"')  # same refund again, without a new declaration
    assert replay.error == "policy"
    assert len([r for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48213"]) == 1


async def test_large_refund_waits_for_approval(op: Driver, sandbox: LiveSandbox, ctx: ToolContext) -> None:
    pay = await refund_form(op, sandbox, "QB-48241", "605.85", "wrong_order")
    facts = {"payment_id": pay, "amount": 605.85, "reason": "wrong_order", "full_order_refund": True}
    blocked = await op.click('button "Confirm refund"', "payments.refund", **facts)
    assert blocked.error == "needs_approval"
    assert "full_order_refund" in blocked.output and "high_value" in blocked.output

    asked = await op.call("request_approval", action="payments.refund", facts=facts, justification="Bag swap")
    request_id = asked.data["request_id"]
    assert [r.id for r in ctx.human.pending] == [request_id]

    ctx.human.decide(request_id, approved=True, approver="priya.supervisor")
    done = await op.click('button "Confirm refund"', "payments.refund", **facts)
    assert done.ok, done.output
    assert [r["amount"] for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48241"] == [60585]


async def test_forbidden_action_is_denied(op: Driver, sandbox: LiveSandbox) -> None:
    flagged = await op.open("ops", "restaurants/REST-02")
    assert flagged.ok
    await op.click('button "Flag for quality review"', "ops.flag_restaurant", restaurant_id="REST-02")
    denied = await op.click(
        'button "Remove quality-review flag"', "ops.unflag_restaurant", restaurant_id="REST-02"
    )
    assert denied.error == "policy" and "not permitted" in denied.output
    sneaky = await op.click('button "Remove quality-review flag"')
    assert sneaky.error == "policy"
    assert next(r for r in sandbox.rows("ops_restaurants") if r["id"] == "REST-02")["flagged"] == 1


# ── write failures are classified, never blindly retried ──


async def test_refund_timeout_is_uncertain_and_cannot_be_replayed(op: Driver, sandbox: LiveSandbox) -> None:
    pay = await refund_form(op, sandbox, "QB-48260", "100", "order_cancelled")
    sandbox.faults(refund_commit_timeout_next=1)
    result = await op.click(
        'button "Confirm refund"', "payments.refund", payment_id=pay, amount=100, reason="order_cancelled"
    )
    assert result.error == "uncertain"
    assert "may or may not have been saved" in result.output
    # The money did move, and the used grant cannot be replayed without a fresh decision.
    assert len([r for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48260"]) == 1
    await refund_form(op, sandbox, "QB-48260", "100", "order_cancelled")
    assert (await op.click('button "Confirm refund"')).error == "policy"


async def test_stale_form_is_rejected_and_grant_returned(op: Driver, sandbox: LiveSandbox) -> None:
    await op.open("support", "tickets/TKT-1001")
    await op.fill('textarea "Reply to customer"', "Refund issued.")
    sandbox.faults(stale_form_next=1)
    first = await op.click('button "Send reply"', "support.reply_to_customer", ticket_id="TKT-1001")
    assert first.error == "rejected"
    await op.open("support", "tickets/TKT-1001")
    await op.fill('textarea "Reply to customer"', "Refund issued.")
    second = await op.click('button "Send reply"', "support.reply_to_customer", ticket_id="TKT-1001")
    assert second.ok, second.output
    assert len([m for m in sandbox.rows("support_messages") if m["body"] == "Refund issued."]) == 1


async def test_session_expiry_on_write_is_rejected_then_succeeds(op: Driver, sandbox: LiveSandbox) -> None:
    await op.open("support", "tickets/TKT-1001")
    await op.fill('textarea "Internal note', "first attempt")
    sandbox.faults(session_expiry_in=1)
    result = await op.click('button "Add internal note"', "support.add_internal_note", ticket_id="TKT-1001")
    assert result.error == "rejected" and "session had expired" in result.output
    assert not [m for m in sandbox.rows("support_messages") if m["body"] == "first attempt"]
    await op.fill('textarea "Internal note', "first attempt")
    retry = await op.click('button "Add internal note"', "support.add_internal_note", ticket_id="TKT-1001")
    assert retry.ok, retry.output


async def test_validation_error_is_rejected(op: Driver, sandbox: LiveSandbox) -> None:
    await op.open("payments", "coupons")
    await op.fill('textbox "Customer ID"', "CUST-9999")
    await op.fill('textbox "Value', "50")
    await op.select('combobox "Reason"', "late_delivery")
    result = await op.click(
        'button "Issue coupon"',
        "payments.issue_coupon",
        customer_id="CUST-9999",
        amount=50,
        reason="late_delivery",
    )
    assert result.error == "rejected" and "No payment history" in result.output


# ── a UI redesign ──


async def test_shifted_layout_is_navigable(op: Driver, sandbox: LiveSandbox) -> None:
    sandbox.faults(layout="shifted")
    pay = payment_id(sandbox, "QB-48213")
    page = await op.open("payments", f"transactions/{pay}")
    assert 'collapsed menu "Payment actions"' in page.output
    assert "Refund amount" not in page.output  # hidden until the menu is opened
    expanded = await op.click('collapsed menu "Payment actions"')
    assert 'button "Process refund"' in expanded.output


# ── evidence ──


async def test_view_attachment_and_screenshot(op: Driver, ctx: ToolContext) -> None:
    await op.open("support", "tickets/TKT-1003")
    attachment = await op.call("view_attachment", url="/support/attachments/1")
    assert attachment.ok and attachment.images and attachment.images[0].read_bytes().startswith(b"\x89PNG")
    shot = await op.call("browser_screenshot", label="Wrong order ticket")
    assert shot.evidence[0].name == "01-wrong-order-ticket.png"
    assert len(ctx.log.of_type("evidence.saved")) == 2


def test_repeated_system_prefix_in_path_is_tolerated(ctx: ToolContext) -> None:
    """Regression: the model sometimes writes path 'ops/customers/X' with system 'ops'."""
    expected = ctx.browser.url_for("ops", "customers/CUST-1001")
    assert ctx.browser.url_for("ops", "ops/customers/CUST-1001") == expected
    assert ctx.browser.url_for("ops", "/ops/customers/CUST-1001") == expected
    assert expected.endswith("/ops/customers/CUST-1001")


async def test_changes_in_another_form_are_reported_as_lost(op: Driver, sandbox: LiveSandbox) -> None:
    """Regression: the operator set the category in the update form, then submitted the note form."""
    await op.open("support", "tickets/TKT-1001")
    await op.select('combobox "Category"', "missing_item")
    await op.fill('textarea "Internal note', "Checked the packing log.")
    result = await op.click('button "Add internal note"', "support.add_internal_note", ticket_id="TKT-1001")
    assert result.ok
    assert "Your changes to Category (in form 'Update ticket') were NOT submitted" in result.output
    assert (
        "Internal note" not in result.output.split("NOT submitted")[0]
    )  # the note's own field was submitted
    ticket = next(t for t in sandbox.rows("support_tickets") if t["id"] == "TKT-1001")
    assert ticket["category"] is None  # the browser really did discard it, as the warning says


async def test_submitting_the_right_form_gives_no_warning(op: Driver, sandbox: LiveSandbox) -> None:
    await op.open("support", "tickets/TKT-1001")
    await op.select('combobox "Category"', "missing_item")
    result = await op.click('button "Update ticket"', "support.update_ticket", ticket_id="TKT-1001")
    assert result.ok and "NOT submitted" not in result.output
    assert (
        next(t for t in sandbox.rows("support_tickets") if t["id"] == "TKT-1001")["category"]
        == "missing_item"
    )
