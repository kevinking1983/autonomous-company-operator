"""The runtime loop: phases, failure handling, budgets, pausing for people, checkpoints.

Brain and verifier are scripted; tools, browser, policy and sandbox are real.
"""

import re
from pathlib import Path

import pytest

from company_operator.company_pack import CompanyPack
from company_operator.runtime import (
    BrainContext,
    NextAction,
    Operator,
    RunState,
    RunStore,
    TaskRequest,
    ToolCall,
)
from company_operator.runtime.models import Budgets
from company_operator.tools import ToolContext, ToolRegistry
from tests.conftest import LiveSandbox
from tests.runtime.scripted import (
    Move,
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


def stale_retry(action: str, facts: dict[str, object]) -> Move:
    """Click the confirm button again using its ref from the earlier confirmation page."""

    def move(ctx: BrainContext) -> NextAction:
        confirm_page = next(o.output for o in ctx.state.observations if "# Confirm refund" in o.output)
        found = re.search(r'button "Confirm refund" \[(e\d+)', confirm_page)
        assert found
        return NextAction(
            kind="tool",
            call=ToolCall(
                tool="browser_click", arguments={"ref": found.group(1), "action": action, "facts": facts}
            ),
        )

    return move


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "runs")


def operator(
    brain: ScriptedBrain,
    registry: ToolRegistry,
    pack: CompanyPack,
    store: RunStore,
    verifier: ScriptedVerifier | None = None,
) -> Operator:
    return Operator(brain, verifier or ScriptedVerifier(True), registry, pack, store, transient_backoff=0)


def new_run(store: RunStore, text: str = "Resolve TKT-1001", **budgets: int) -> RunState:
    state = store.create(TaskRequest(text=text, source="ticket", ticket_id="TKT-1001"))
    state.budgets = Budgets(**budgets)
    return state


def phases(ctx: ToolContext) -> list[str]:
    return [e.data["to"] for e in ctx.log.of_type("phase")]


def payment_id(sandbox: LiveSandbox, order_id: str) -> str:
    return str(next(p["id"] for p in sandbox.rows("pay_payments") if p["order_id"] == order_id))


async def test_missing_item_end_to_end(
    ctx: ToolContext, registry: ToolRegistry, pack: CompanyPack, store: RunStore, sandbox: LiveSandbox
) -> None:
    pay = payment_id(sandbox, "QB-48213")
    brain = ScriptedBrain(
        reads=[ToolCall(tool="browser_open", arguments={"system": "support", "path": "tickets/TKT-1001"})],
        task=contract("Refund of ₹60 exists for QB-48213", "Customer replied", "Ticket resolved"),
        plans=[plan("investigate", "refund", "reply")],
        moves=[
            open_("ops", "orders/QB-48213", step="investigate"),
            done("investigate", "Coke 500ml ₹60 NOT PACKED"),
            open_("payments", f"transactions/{pay}", step="refund"),
            fill('textbox "Refund amount', "60"),
            select('combobox "Reason"', "missing_item"),
            click('button "Review refund"'),
            click(
                'button "Confirm refund"', "payments.refund", payment_id=pay, amount=60, reason="missing_item"
            ),
            done("refund", "refund created"),
            open_("support", "tickets/TKT-1001", step="reply"),
            fill('textarea "Reply to customer"', "Hi Ananya, we refunded ₹60 for the missing Coke."),
            select('combobox "After sending', "resolved"),
            click('button "Send reply"', "support.reply_to_customer", ticket_id="TKT-1001"),
            done("reply", "replied and resolved"),
        ],
    )
    state = await operator(brain, registry, pack, store).run(new_run(store), ctx)

    assert state.phase == "completed", state.outcome_reason
    assert phases(ctx) == ["plan", "execute", "observe", "execute", "observe", "execute", "observe", "execute", "observe",
                           "execute", "observe", "execute", "observe", "execute", "observe", "execute", "observe",
                           "execute", "observe", "execute", "observe", "execute", "verify", "complete", "completed"]  # fmt: skip
    assert state.observations[0].phase == "understand"
    assert all(s.status == "done" for s in state.plan.steps)  # type: ignore[union-attr]
    assert [
        (r["amount"], r["reason_code"]) for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48213"
    ] == [(6000, "missing_item")]
    ticket = next(t for t in sandbox.rows("support_tickets") if t["id"] == "TKT-1001")
    assert ticket["status"] == "resolved"
    # The checkpoint on disk matches the final state.
    assert store.load(state.run_id).phase == "completed"


async def test_uncertain_refund_forces_reobservation(
    ctx: ToolContext, registry: ToolRegistry, pack: CompanyPack, store: RunStore, sandbox: LiveSandbox
) -> None:
    pay = payment_id(sandbox, "QB-48260")
    facts = {"payment_id": pay, "amount": 100, "reason": "order_cancelled"}
    sandbox.faults(refund_commit_timeout_next=1)
    brain = ScriptedBrain(
        plans=[plan("refund")],
        moves=[
            open_("payments", f"transactions/{pay}", step="refund"),
            fill('textbox "Refund amount', "100"),
            select('combobox "Reason"', "order_cancelled"),
            click('button "Review refund"'),
            click('button "Confirm refund"', "payments.refund", **facts),  # → 504: uncertain
            stale_retry("payments.refund", facts),  # naive retry with the old button: refused by the runtime
            open_("payments", "refunds?q=QB-48260"),  # look first...
            done("refund", "refund RF exists despite the timeout"),  # ...and find it already happened
        ],
    )
    state = await operator(brain, registry, pack, store).run(new_run(store), ctx)

    assert state.phase == "completed"
    errors = [o.error for o in state.observations]
    assert errors[4:6] == ["uncertain", "policy"]
    assert "unknown outcome" in state.observations[5].output
    assert [e.data["rule"] for e in ctx.log.of_type("adapt.decision")] == [
        "reobserve_before_retry",
        "respect_policy",
    ]
    assert not state.must_reobserve
    assert (
        len([r for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48260"]) == 1
    )  # no double refund


async def test_persistent_outage_escalates_instead_of_looping(
    ctx: ToolContext, registry: ToolRegistry, pack: CompanyPack, store: RunStore, sandbox: LiveSandbox
) -> None:
    sandbox.faults(error_rate=1.0, systems=["ops"])
    brain = ScriptedBrain(
        plans=[plan("investigate")], moves=[open_("ops", "orders/QB-48213", step="investigate")] * 20
    )
    state = await operator(brain, registry, pack, store).run(
        new_run(store, max_attempts_per_step=2, max_replans=1), ctx
    )
    assert state.phase == "escalated"
    assert "Replan budget exhausted" in state.outcome_reason
    assert [e.data["rule"] for e in ctx.log.of_type("adapt.decision")][:2] == [
        "retry_after_backoff",
        "attempts_exhausted",
    ]


async def test_approval_pauses_and_resumes_from_checkpoint(
    ctx: ToolContext, registry: ToolRegistry, pack: CompanyPack, store: RunStore, sandbox: LiveSandbox
) -> None:
    pay = payment_id(sandbox, "QB-48241")
    facts = {"payment_id": pay, "amount": 605.85, "reason": "wrong_order", "full_order_refund": True}
    moves = [
        open_("payments", f"transactions/{pay}", step="refund"),
        fill('textbox "Refund amount', "605.85"),
        select('combobox "Reason"', "wrong_order"),
        click('button "Review refund"'),
        click('button "Confirm refund"', "payments.refund", **facts),  # → needs_approval
        tool(
            "request_approval", action="payments.refund", facts=facts, justification="Bag swap with QB-48244"
        ),
    ]
    first = operator(ScriptedBrain(plans=[plan("refund")], moves=moves), registry, pack, store)
    paused = await first.run(new_run(store), ctx)
    assert paused.phase == "awaiting_human" and paused.pending_human

    # A new process picks the run up from disk.
    restored = store.load(paused.run_id)
    assert restored.phase == "awaiting_human" and restored.pending_human == paused.pending_human
    after_approval = [
        click('button "Confirm refund"', "payments.refund", **facts),
        done("refund", "full refund issued after approval"),
    ]
    second = operator(ScriptedBrain(plans=[plan("refund")], moves=after_approval), registry, pack, store)
    still_waiting = await second.resume(restored, ctx)
    assert still_waiting.phase == "awaiting_human"

    ctx.human.decide(restored.pending_human or "", approved=True, approver="priya.supervisor")
    finished = await second.resume(restored, ctx)
    assert finished.phase == "completed", finished.outcome_reason
    assert "APPROVED by priya.supervisor" in next(
        o.output for o in finished.observations if o.call.tool == "human_response"
    )
    assert [r["amount"] for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48241"] == [60585]


async def test_failed_verification_triggers_replan(
    ctx: ToolContext, registry: ToolRegistry, pack: CompanyPack, store: RunStore, sandbox: LiveSandbox
) -> None:
    brain = ScriptedBrain(
        plans=[plan("s1"), plan("s1", "fix")], moves=[open_("support", "tickets/TKT-1001", step="s1")]
    )
    verifier = ScriptedVerifier(False, True)
    state = await operator(brain, registry, pack, store, verifier).run(new_run(store), ctx)
    assert state.phase == "completed"
    assert state.plan is not None and state.plan.version == 2
    assert state.plan.step("s1").status == "done"  # type: ignore[union-attr]  # earlier work is kept
    assert [e.data["passed"] for e in ctx.log.of_type("verify.result")] == [False, True]


async def test_unverifiable_outcome_escalates(
    ctx: ToolContext, registry: ToolRegistry, pack: CompanyPack, store: RunStore, sandbox: LiveSandbox
) -> None:
    brain = ScriptedBrain(moves=[open_("support", "tickets/TKT-1001", step="s1")])
    state = await operator(brain, registry, pack, store, ScriptedVerifier(False)).run(new_run(store), ctx)
    assert state.phase == "escalated" and "could not be verified" in state.outcome_reason


async def test_understand_may_only_read(
    ctx: ToolContext, registry: ToolRegistry, pack: CompanyPack, store: RunStore, sandbox: LiveSandbox
) -> None:
    sneaky = ToolCall(tool="browser_click", arguments={"ref": "e1", "action": "payments.refund", "facts": {}})
    brain = ScriptedBrain([], reads=[sneaky])
    state = await operator(brain, registry, pack, store).run(new_run(store), ctx)
    assert state.phase == "completed"
    assert not [o for o in state.observations if o.phase == "understand"]
    assert [e.data["tool"] for e in ctx.log.of_type("understand.refused")] == ["browser_click"]


class BrokenBrain(ScriptedBrain):
    async def plan(self, ctx):  # type: ignore[no-untyped-def]
        raise ValueError("model returned malformed JSON")


async def test_brain_failure_fails_run_cleanly(
    ctx: ToolContext, registry: ToolRegistry, pack: CompanyPack, store: RunStore, sandbox: LiveSandbox
) -> None:
    state = await operator(BrokenBrain([]), registry, pack, store).run(new_run(store), ctx)
    assert state.phase == "failed" and "malformed JSON" in state.outcome_reason
    assert len(ctx.log.of_type("brain.error")) == 3


async def test_tool_budget(
    ctx: ToolContext, registry: ToolRegistry, pack: CompanyPack, store: RunStore, sandbox: LiveSandbox
) -> None:
    brain = ScriptedBrain(moves=[tool("check_policy", "s1", action="payments.read")] * 10)
    state = await operator(brain, registry, pack, store).run(new_run(store, max_tool_calls=3), ctx)
    assert state.phase == "escalated" and "budget" in state.outcome_reason
    assert state.counters.tool_calls == 4
