"""The eval harness end to end: real sandbox, real browser, a scripted brain instead of a model."""

from pathlib import Path

from company_operator.company_pack import CompanyPack
from company_operator.config import Settings
from company_operator.evals.harness import EvalHarness, EvalStore, scorecard
from company_operator.evals.world import Sandbox
from company_operator.llm.base import LLMError
from company_operator.memory.store import OperatorDB
from company_operator.runtime import Operator, RunStore, ToolCall
from company_operator.tools import default_registry
from tests.conftest import CONTROL_KEY, LiveSandbox
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
)


def refund_script(pay: str, amount: int) -> ScriptedBrain:
    """Resolve TKT-1001 by refunding `amount` rupees for the missing ₹60 Coke."""
    return ScriptedBrain(
        reads=[ToolCall(tool="browser_open", arguments={"system": "support", "path": "tickets/TKT-1001"})],
        task=contract("Refund exists", "Customer replied", "Ticket resolved"),
        plans=[plan("refund", "reply")],
        moves=[
            open_("payments", f"transactions/{pay}", step="refund"),
            fill('textbox "Refund amount', str(amount)),
            select('combobox "Reason"', "missing_item"),
            click('button "Review refund"'),
            click(
                'button "Confirm refund"',
                "payments.refund",
                payment_id=pay,
                amount=amount,
                reason="missing_item",
            ),
            done("refund", "refund created"),
            open_("support", "tickets/TKT-1001", step="reply"),
            fill('textarea "Reply to customer"', f"Hi Ananya, we refunded ₹{amount} for the missing Coke."),
            select('combobox "After sending', "resolved"),
            click('button "Send reply"', "support.reply_to_customer", ticket_id="TKT-1001"),
            done("reply", "replied and resolved"),
        ],
    )


async def test_ground_truth_decides_not_the_operator(
    sandbox: LiveSandbox, pack: CompanyPack, tmp_path: Path
) -> None:
    pay = str(next(p["id"] for p in sandbox.rows("pay_payments") if p["order_id"] == "QB-48213"))
    brains = iter([refund_script(pay, 60), refund_script(pay, 70)])  # right, then ₹10 too much

    def make_operator(runs: RunStore, db: OperatorDB) -> Operator:
        # The verifier always says "all good": only the ground truth can catch the ₹70 run.
        return Operator(
            next(brains), ScriptedVerifier(True), default_registry(), pack, runs, db=db, transient_backoff=0
        )

    store = EvalStore(tmp_path / "evals")
    settings = Settings(sandbox_url=sandbox.url, data_dir=tmp_path)
    harness = EvalHarness(
        settings, pack, store, Sandbox(sandbox.url, CONTROL_KEY), make_operator, progress=lambda _: None
    )
    eval_id = store.create(
        [("missing_item", "clean"), ("missing_item", "flaky")], label="test", models=["scripted"]
    )
    meta = await harness.run(eval_id)

    assert meta["status"] == "completed"
    right, wrong = sorted(store.results(eval_id), key=lambda r: r.profile)
    assert (right.profile, right.status) == ("clean", "pass"), [c for c in right.checks if not c.passed]
    assert (wrong.profile, wrong.status) == ("flaky", "fail")
    assert [c.id for c in wrong.checks if not c.passed] == ["money.amount"]
    assert wrong.faults_injected > 0  # the flaky profile really was switched on
    assert wrong.verified is True and wrong.verifier_agrees is False
    assert right.metrics["tool_calls"] >= 8 and right.run_id
    assert (store.dir(eval_id) / "scorecard.md").read_text().count("| missing_item |") == 2
    card = scorecard(store.results(eval_id))
    assert card["verifier_false_passes"] == 1 and card["unsafe_runs"] == 0

    # Resuming a finished eval runs nothing again (the scripted brains are used up: it would fail).
    assert (await harness.run(eval_id))["status"] == "completed"
    assert len(store.results(eval_id)) == 2


async def test_model_outage_pauses_the_eval_instead_of_failing_cases(
    sandbox: LiveSandbox, pack: CompanyPack, tmp_path: Path
) -> None:
    def make_operator(runs: RunStore, db: OperatorDB) -> Operator:
        raise LLMError("All models failed: gemini #1: HTTP 429: You exceeded your current quota")

    store = EvalStore(tmp_path / "evals")
    harness = EvalHarness(
        Settings(sandbox_url=sandbox.url, data_dir=tmp_path),
        pack,
        store,
        Sandbox(sandbox.url, CONTROL_KEY),
        make_operator,
        progress=lambda _: None,
    )
    pairs = [("missing_item", "clean"), ("late_delivery", "clean"), ("refund_status", "clean")]
    eval_id = store.create(pairs, label="test", models=["gemini"])
    meta = await harness.run(eval_id)
    # Two outages in a row: stop, keep what ran as "not scored", and leave the rest for a resume.
    assert meta["status"] == "paused" and meta["reason"] == "model unavailable"
    assert [(r.status, r.error) for r in store.results(eval_id)] == [("error", "model unavailable")] * 2
    assert scorecard(store.results(eval_id))["scored"] == 0
