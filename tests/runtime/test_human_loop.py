"""Pausing for people, resuming in a new process, handing over, and learning from feedback."""

from pathlib import Path

import pytest

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.memory.store import OperatorDB
from company_operator.policy import PolicyEngine
from company_operator.runtime import Operator, RunStore, TaskRequest
from company_operator.tools import ToolContext, ToolRegistry
from company_operator.tools.human import HumanChannel, reply_arrived
from tests.conftest import LiveSandbox
from tests.runtime.scripted import (
    ScriptedBrain,
    ScriptedVerifier,
    click,
    contract,
    done,
    fill,
    open_,
    plan,
    select,
    tool,
)


@pytest.fixture
def db(tmp_path: Path) -> OperatorDB:
    return OperatorDB(tmp_path / "operator.db")


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "runs")


def payment_id(sandbox: LiveSandbox, order_id: str) -> str:
    return str(next(p["id"] for p in sandbox.rows("pay_payments") if p["order_id"] == order_id))


def use_db(ctx: ToolContext, db: OperatorDB, run_id: str) -> None:
    ctx.human.db = db
    ctx.human.run_id = run_id


# ── durable requests ──


def test_requests_survive_a_new_process(db: OperatorDB, pack: CompanyPack) -> None:
    first = HumanChannel(PolicyEngine(pack, EventLog()), EventLog(), db, run_id="r1")
    decision = first.policy.evaluate(
        "payments.refund", {"payment_id": "PAY-1", "amount": 900, "reason": "wrong_order"}
    )
    request = first.request_approval(decision, "Bag swap", context={"page": "/x"})
    assert request.id == "H-1001"

    # Another process (e.g. the API) decides; the run's process rebuilds the grant from the stored request.
    api = HumanChannel(PolicyEngine(pack, EventLog()), EventLog(), OperatorDB(db.path))
    api.decide(request.id, approved=True, approver="priya.supervisor")
    later = HumanChannel(PolicyEngine(pack, EventLog()), EventLog(), OperatorDB(db.path), run_id="r1")
    grant = later.grant_for(later.get(request.id))
    assert grant.source == "approval:H-1001 by priya.supervisor"
    assert later.policy.guard(
        "POST",
        "/payments/transactions/PAY-1/refund/confirm",
        {"amount": "90000", "reason_code": "wrong_order"},
    ).allowed


def test_rejection_reason_becomes_a_learned_fact(db: OperatorDB, pack: CompanyPack) -> None:
    channel = HumanChannel(PolicyEngine(pack, EventLog()), EventLog(), db, run_id="r1")
    decision = channel.policy.evaluate(
        "payments.refund",
        {"payment_id": "PAY-1", "amount": 249, "reason": "missing_item", "customer_claims_30d": 4},
    )
    request = channel.request_approval(decision, "5th claim")
    channel.decide(
        request.id,
        approved=False,
        approver="priya.supervisor",
        note="No refunds for repeat claimants without photo proof.",
        remember=True,
    )
    [fact] = db.facts()
    assert fact.text == "No refunds for repeat claimants without photo proof."
    assert "H-1001" in fact.source and "rejected by priya.supervisor" in fact.source
    with pytest.raises(ValueError, match="already rejected"):
        channel.decide(request.id, approved=True, approver="someone")


def test_reply_detection_ignores_refs_and_status_flips() -> None:
    before = 'Status: pending_customer\nbutton "Send reply" [e12]\nImran Khan · customer\nMy order was bad.'
    flip = 'Status: open\nbutton "Send reply" [e14]\nImran Khan · customer\nMy order was bad.'
    assert reply_arrived(before, flip) is None
    answered = flip + "\nThe dal makhani was missing completely, and the naan was burnt."
    assert (
        reply_arrived(before, answered)
        == "Status: open\nThe dal makhani was missing completely, and the naan was burnt."
    )


# ── the runtime with people ──


async def test_customer_reply_resumes_the_run(
    ctx: ToolContext,
    registry: ToolRegistry,
    pack: CompanyPack,
    store: RunStore,
    sandbox: LiveSandbox,
    db: OperatorDB,
) -> None:
    sandbox.control.post("/_control/reset")
    state = store.create(TaskRequest(text="Resolve TKT-1012", ticket_id="TKT-1012"))
    use_db(ctx, db, state.run_id)
    brain = ScriptedBrain(
        plans=[plan("ask", "resolve")],
        moves=[
            open_("support", "tickets/TKT-1012", step="ask"),
            fill('textarea "Reply to customer"', "Sorry! What exactly was wrong with the order?"),
            select('combobox "After sending', "pending_customer"),
            click('button "Send reply"', "support.reply_to_customer", ticket_id="TKT-1012"),
            tool("wait_for_reply", record_id="TKT-1012", about="What was wrong with the order"),
            done("ask", "customer answered"),
            done("resolve", "handled"),
        ],
    )
    op = Operator(brain, ScriptedVerifier(True), registry, pack, store, db=db, transient_backoff=0)
    paused = await op.run(state, ctx)
    assert paused.phase == "awaiting_human"
    assert db.get_request(paused.pending_human or "").kind == "external_reply"

    assert (await op.resume(paused, ctx)).phase == "awaiting_human"  # nothing new yet
    sandbox.control.post(
        "/_control/tickets/TKT-1012/customer-message",
        json={"body": "The dal makhani was missing completely, and the naan was burnt."},
    )
    finished = await op.resume(paused, ctx)
    assert finished.phase == "completed"
    reply = next(o for o in finished.observations if o.call.tool == "human_response")
    assert "dal makhani was missing" in reply.output


async def test_escalation_hands_over_first(
    ctx: ToolContext,
    registry: ToolRegistry,
    pack: CompanyPack,
    store: RunStore,
    sandbox: LiveSandbox,
    db: OperatorDB,
) -> None:
    state = store.create(TaskRequest(text="Resolve TKT-1001", ticket_id="TKT-1001"))
    use_db(ctx, db, state.run_id)
    handover = [
        open_("support", "tickets/TKT-1001"),
        fill(
            'textarea "Internal note', "Stopped: could not verify. Checked order QB-48213. No changes made."
        ),
        click('button "Add internal note"', "support.add_internal_note", ticket_id="TKT-1001"),
        select('combobox "Status"', "on_hold"),
        click('button "Update ticket"', "support.update_ticket", ticket_id="TKT-1001"),
        done("handover", "on hold with note"),
    ]
    brain = ScriptedBrain(moves=[open_("ops", "orders/QB-48213", step="s1"), done("s1"), *handover])
    op = Operator(brain, ScriptedVerifier(False), registry, pack, store, db=db, transient_backoff=0)
    final = await op.run(state, ctx)

    assert final.phase == "escalated" and "could not be verified" in final.outcome_reason
    assert final.plan is not None and final.plan.step("handover") is not None
    ticket = next(t for t in sandbox.rows("support_tickets") if t["id"] == "TKT-1001")
    assert ticket["status"] == "on_hold"
    [episode] = db.episodes()
    assert episode.outcome == "escalated" and {c["action"] for c in episode.changes} == {
        "support.add_internal_note",
        "support.update_ticket",
    }


async def test_learned_facts_and_episodes_reach_the_brain(
    ctx: ToolContext,
    registry: ToolRegistry,
    pack: CompanyPack,
    store: RunStore,
    sandbox: LiveSandbox,
    db: OperatorDB,
) -> None:
    db.add_fact(
        "No refunds for repeat claimants without photo proof.", "approval H-1001 rejected by priya.supervisor"
    )
    first = store.create(TaskRequest(text="Resolve TKT-1001", ticket_id="TKT-1001"))
    op = Operator(
        ScriptedBrain(
            moves=[open_("support", "tickets/TKT-1001", step="s1")],
            task=contract("Coke refunded").model_copy(update={"category": "missing_item"}),
        ),
        ScriptedVerifier(True),
        registry,
        pack,
        store,
        db=db,
    )
    await op.run(first, ctx)
    second = store.create(TaskRequest(text="Resolve TKT-1011", ticket_id="TKT-1011"))
    assert second.contract is None
    second.contract = first.contract
    context = op.context(second)
    assert any(f.text.startswith("No refunds for repeat claimants") for f in context.facts)
    assert [e.run_id for e in context.episodes] == [first.run_id]


async def test_rejection_leads_to_a_revised_contract(
    ctx: ToolContext,
    registry: ToolRegistry,
    pack: CompanyPack,
    store: RunStore,
    sandbox: LiveSandbox,
    db: OperatorDB,
) -> None:
    """Regression: after a rejected approval the old criteria ("refund issued") no longer describe done."""
    pay = payment_id(sandbox, "QB-48275")
    facts = {"payment_id": pay, "amount": 249, "reason": "missing_item", "customer_claims_30d": 4}
    state = store.create(TaskRequest(text="Resolve TKT-1010", ticket_id="TKT-1010"))
    use_db(ctx, db, state.run_id)
    brain = ScriptedBrain(
        task=contract("Refund of ₹249 exists"),
        plans=[plan("refund"), plan("hold")],
        moves=[
            tool(
                "request_approval", "refund", action="payments.refund", facts=facts, justification="5th claim"
            ),
            done("hold", "ticket on hold"),
        ],
    )
    op = Operator(brain, ScriptedVerifier(True), registry, pack, store, db=db, transient_backoff=0)
    paused = await op.run(state, ctx)
    ctx.human.decide(
        paused.pending_human or "", approved=False, approver="priya.supervisor", note="No refund."
    )
    brain.task = contract("Ticket is on hold for the fraud team", "No refund exists for QB-48275")
    finished = await op.resume(paused, ctx)

    assert finished.phase == "completed"
    assert ctx.log.of_type("contract.revised")
    assert finished.contract is not None
    assert finished.contract.success_criteria[0].text == "Ticket is on hold for the fraud team"
    assert not [r for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48275"]


def test_supervisor_can_approve_a_lower_amount_only(db: OperatorDB, pack: CompanyPack) -> None:
    channel = HumanChannel(PolicyEngine(pack, EventLog()), EventLog(), db, run_id="r1")
    facts = {"payment_id": "PAY-1", "amount": 605.85, "reason": "wrong_order", "full_order_refund": True}
    request = channel.request_approval(channel.policy.evaluate("payments.refund", facts), "Bag swap")
    with pytest.raises(ValueError, match="at most the requested"):
        channel.decide(request.id, approved=True, approver="priya.supervisor", amount=700)
    decided = channel.decide(request.id, approved=True, approver="priya.supervisor", amount=300)
    assert decided.facts["amount"] == 300 and decided.facts["full_order_refund"] is False
    assert decided.context["requested_amount"] == 605.85

    # The grant covers the approved amount and nothing more.
    channel.grant_for(decided)
    confirm = "/payments/transactions/PAY-1/refund/confirm"
    assert not channel.policy.guard(
        "POST", confirm, {"amount": "60585", "reason_code": "wrong_order"}
    ).allowed
    assert channel.policy.guard("POST", confirm, {"amount": "30000", "reason_code": "wrong_order"}).allowed

    question = channel.ask("Which order?")
    with pytest.raises(ValueError, match="not an approval"):
        channel.decide(question.id, approved=True, approver="x", amount=10)


async def test_lower_amount_tells_the_operator_and_revises_the_contract(
    ctx: ToolContext,
    registry: ToolRegistry,
    pack: CompanyPack,
    store: RunStore,
    sandbox: LiveSandbox,
    db: OperatorDB,
) -> None:
    facts = {"payment_id": payment_id(sandbox, "QB-48241"), "amount": 605.85, "reason": "wrong_order"}
    state = store.create(TaskRequest(text="Resolve TKT-1003", ticket_id="TKT-1003"))
    use_db(ctx, db, state.run_id)
    brain = ScriptedBrain(
        task=contract("Refund of ₹605.85 exists"),
        plans=[plan("refund"), plan("refund")],
        moves=[
            tool(
                "request_approval", "refund", action="payments.refund", facts=facts, justification="Bag swap"
            ),
            done("refund", "refund of ₹400 issued"),
        ],
    )
    op = Operator(brain, ScriptedVerifier(True), registry, pack, store, db=db, transient_backoff=0)
    paused = await op.run(state, ctx)
    ctx.human.decide(paused.pending_human or "", approved=True, approver="priya.supervisor", amount=400)
    brain.task = contract("Refund of ₹400 exists")
    finished = await op.resume(paused, ctx)

    response = next(o for o in finished.observations if o.call.tool == "human_response")
    assert "LOWER amount: 400 instead of 605.85" in response.output
    assert ctx.log.of_type("contract.revised")
    assert (
        finished.contract is not None
        and finished.contract.success_criteria[0].text == "Refund of ₹400 exists"
    )
