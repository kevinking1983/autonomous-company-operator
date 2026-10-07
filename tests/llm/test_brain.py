"""The language-model brain: what it sends and how it turns answers into decisions (fake model, no network)."""

from pathlib import Path
from typing import Any

import pytest

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.llm import FunctionCall, LLMRequest, LLMResponse
from company_operator.planning import prompts
from company_operator.planning.llm_brain import LLMBrain
from company_operator.runtime import (
    BrainContext,
    Criterion,
    Plan,
    PlanStep,
    RunState,
    TaskContract,
    TaskRequest,
    ToolCall,
)
from company_operator.runtime.models import Observation
from company_operator.tools import default_registry


class FakeLLM:
    def __init__(self, *answers: list[FunctionCall] | str) -> None:
        self.answers = list(answers)
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, str):
            return LLMResponse(text=answer, calls=[], model="fake")
        return LLMResponse(text="", calls=answer, model="fake")


def call(name: str, **arguments: Any) -> FunctionCall:
    return FunctionCall(name=name, arguments=arguments)


def context(pack: CompanyPack, state: RunState | None = None, log: EventLog | None = None) -> BrainContext:
    state = state or RunState(
        run_id="r1",
        request=TaskRequest(text="Resolve support ticket TKT-1001.", source="ticket", ticket_id="TKT-1001"),
    )
    return BrainContext(
        state=state,
        pack=pack,
        tools=default_registry().schemas(),
        sops=pack.sops_for("missing_item"),
        facts=list(pack.facts),
        log=log,
    )


def page(seq: int, url: str, body: str = "", images: list[str] | None = None) -> Observation:
    return Observation(
        seq=seq, phase="execute", step_id="s1", call=ToolCall(tool="browser_open"), ok=True,
        output=f"URL: {url}\nTitle: t\n\n{body}", images=images or [],
    )  # fmt: skip


def executing_state() -> RunState:
    state = RunState(run_id="r1", request=TaskRequest(text="Resolve TKT-1001"))
    state.contract = TaskContract(
        outcome="refund", success_criteria=[Criterion(id="c1", text="Refund exists")]
    )
    state.plan = Plan(steps=[PlanStep(id="refund", goal="Refund the Coke", expected="RF exists")])
    state.current_step = "refund"
    return state


# ── what the model is told ──


def test_system_prompt_is_built_from_the_company_pack(pack: CompanyPack) -> None:
    text = prompts.system_prompt(pack)
    assert "Customer Support Operations Associate at QuickBite" in text
    assert "payments.refund [money]" in text and "Facts: payment_id, amount, reason." in text
    assert "ops.unflag_restaurant" not in text  # forbidden actions are not offered
    assert "min_minutes_late: 30" in text and "high_value: Any refund or coupon above ₹500." in text
    assert "Never reveal another customer's name" in text
    assert "Address the customer by first name" in text  # tone guide


def test_recent_pages_shows_several_in_full_newest_first(pack: CompanyPack) -> None:
    state = executing_state()
    state.observations = [
        page(1, "http://x/ops/orders/QB-48213", "Coke 500ml | NOT PACKED"),
        page(2, "http://x/payments/transactions/PAY-1", "Refundable balance: ₹584.85"),
    ]
    text = prompts.recent_pages(state)
    assert (
        text.index("CURRENT PAGE")
        < text.index("Refundable balance")
        < text.index("earlier page")
        < text.index("NOT PACKED")
    )


async def test_images_from_attachments_are_sent(pack: CompanyPack, tmp_path: Path) -> None:
    photo = tmp_path / "bag.png"
    photo.write_bytes(b"\x89PNG")
    state = executing_state()
    state.observations = [page(1, "http://x/support/tickets/TKT-1003", images=[str(photo)])]
    llm = FakeLLM([call("step_done", step_id="refund", outcome="ok")])
    await LLMBrain(llm).next_action(context(pack, state))
    assert [i.path for i in llm.requests[0].images] == [photo]


# ── understand ──


async def test_understand_reads_then_commits(pack: CompanyPack) -> None:
    llm = FakeLLM(
        [call("browser_open", system="support", path="tickets/TKT-1001", _why="read it"), call("browser_open", system="ops", path="orders/QB-48213")],
        [call("commit_contract", outcome="Refund the Coke", category="missing_item", sop_ids=["missing_item", "made_up"],
              success_criteria=["Refund of ₹60 exists", " ", "Ticket resolved"], assumptions=["Customer is honest"])],
    )  # fmt: skip
    brain = LLMBrain(llm)
    first = await brain.understand(context(pack))
    assert first.kind == "read"
    assert [(r.tool, r.arguments) for r in first.reads] == [
        ("browser_open", {"system": "support", "path": "tickets/TKT-1001"}),  # meta arguments stripped
        ("browser_open", {"system": "ops", "path": "orders/QB-48213"}),
    ]
    second = await brain.understand(context(pack))
    assert second.contract is not None
    assert [c.text for c in second.contract.success_criteria] == ["Refund of ₹60 exists", "Ticket resolved"]
    assert second.contract.sop_ids == ["missing_item"]  # unknown SOP ids are dropped
    offered = {t.name for t in llm.requests[0].tools}
    assert "commit_contract" in offered and "browser_open" in offered and "browser_fill" not in offered
    assert llm.requests[0].tool_mode == "any"


async def test_contract_without_criteria_is_rejected(pack: CompanyPack) -> None:
    with pytest.raises(ValueError, match="success criteria"):
        await LLMBrain(FakeLLM([call("commit_contract", outcome="x", success_criteria=[])])).understand(
            context(pack)
        )


# ── plan ──


async def test_plan(pack: CompanyPack) -> None:
    llm = FakeLLM([call("submit_plan", rationale="r", steps=[{"id": "check", "goal": "g", "expected": "e"}])])
    plan = await LLMBrain(llm).plan(context(pack, executing_state()))
    assert [s.id for s in plan.steps] == ["check"] and plan.rationale == "r"
    assert llm.requests[0].tools[0].name == "submit_plan"


async def test_malformed_plan_raises(pack: CompanyPack) -> None:
    with pytest.raises(ValueError, match="Malformed plan"):
        await LLMBrain(FakeLLM([call("submit_plan", steps=[{"goal": "no id"}])])).plan(
            context(pack, executing_state())
        )


# ── next action ──


async def test_tool_call_with_meta(pack: CompanyPack) -> None:
    llm = FakeLLM([call("browser_open", system="ops", path="orders/QB-48213", _step="refund", _expect="order visible",
                        _why="check items", _remember={"order_id": "QB-48213"})])  # fmt: skip
    action = await LLMBrain(llm).next_action(context(pack, executing_state()))
    assert action.kind == "tool" and action.call is not None
    assert action.call.arguments == {"system": "ops", "path": "orders/QB-48213"}
    assert (action.step_id, action.expectation, action.note, action.remember) == (
        "refund", "order visible", "check items", {"order_id": "QB-48213"},
    )  # fmt: skip
    schema = next(t for t in llm.requests[0].tools if t.name == "browser_click").parameters
    assert {"_step", "_expect", "_why", "_remember", "ref", "action", "facts"} <= set(schema["properties"])


async def test_form_batch(pack: CompanyPack) -> None:
    llm = FakeLLM([
        call("browser_fill", ref="e5", value="60"),
        call("browser_select", ref="e6", option="missing_item"),
        call("browser_click", ref="e8", _expect="confirmation page"),
    ])  # fmt: skip
    action = await LLMBrain(llm).next_action(context(pack, executing_state()))
    assert action.call is not None and action.call.tool == "browser_fill"
    assert [c.tool for c in action.then] == ["browser_select", "browser_click"]
    assert action.expectation == "confirmation page"


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (
            call(
                "step_done", step_id="refund", outcome="RF-70012 created", _remember={"refund_id": "RF-70012"}
            ),
            "step_done",
        ),
        (call("step_failed", step_id="refund", reason="no payment"), "step_failed"),
        (call("replan", reason="order was cancelled"), "replan"),
        (call("finish", reason="already refunded"), "finish"),
    ],
)
async def test_control_calls(pack: CompanyPack, answer: FunctionCall, kind: str) -> None:
    action = await LLMBrain(FakeLLM([answer])).next_action(context(pack, executing_state()))
    assert action.kind == kind
    if kind == "step_done":
        assert action.remember == {"refund_id": "RF-70012"}


async def test_no_function_call_raises(pack: CompanyPack) -> None:
    with pytest.raises(ValueError, match="without a function call"):
        await LLMBrain(FakeLLM("I think we should refund")).next_action(context(pack, executing_state()))


# ── summary and logging ──


async def test_summary_is_plain_text_and_calls_are_logged(pack: CompanyPack) -> None:
    log = EventLog()
    llm = FakeLLM("Refunded ₹60 for the missing Coke (RF-70012).")
    summary = await LLMBrain(llm).summarize(context(pack, executing_state(), log))
    assert summary.startswith("Refunded ₹60")
    assert llm.requests[0].tool_mode == "none"
    [event] = log.of_type("llm.call")
    assert event.data["purpose"] == "summarize" and event.data["model"] == "fake"
