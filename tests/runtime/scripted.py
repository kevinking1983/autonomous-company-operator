"""Test doubles for the runtime's thinking phases.

A ScriptedBrain replays a fixed sequence of moves. Each move is a function
of the BrainContext, so it can read element refs from the latest page. It
exercises the runtime's machinery (phases, failure handling, budgets,
checkpoints) with real tools against the real sandbox. It is not autonomy;
that is the language-model brain's job.
"""

import re
from collections.abc import Callable
from typing import Any

from company_operator.runtime import (
    BrainContext,
    Criterion,
    CriterionResult,
    NextAction,
    Plan,
    PlanStep,
    RunState,
    TaskContract,
    ToolCall,
    UnderstandDecision,
    Verification,
)
from company_operator.tools import ToolContext

Move = Callable[[BrainContext], NextAction]


def page(ctx: BrainContext) -> str:
    for obs in reversed(ctx.state.observations):
        if obs.output.startswith("URL: "):
            return obs.output
    raise AssertionError("no page observed yet")


def ref(ctx: BrainContext, line_start: str) -> str:
    for line in page(ctx).splitlines():
        if line.strip().startswith(line_start):
            found = re.search(r"\[(e\d+)", line)
            if found:
                return found.group(1)
    raise AssertionError(f"{line_start!r} not on page:\n{page(ctx)}")


def tool(name: str, step: str | None = None, **arguments: Any) -> Move:
    return lambda ctx: NextAction(kind="tool", step_id=step, call=ToolCall(tool=name, arguments=arguments))


def open_(system: str, path: str, step: str | None = None) -> Move:
    return tool("browser_open", step, system=system, path=path)


def fill(line_start: str, value: str) -> Move:
    return lambda ctx: NextAction(
        kind="tool",
        call=ToolCall(tool="browser_fill", arguments={"ref": ref(ctx, line_start), "value": value}),
    )


def select(line_start: str, option: str) -> Move:
    return lambda ctx: NextAction(
        kind="tool",
        call=ToolCall(tool="browser_select", arguments={"ref": ref(ctx, line_start), "option": option}),
    )


def click(line_start: str, action: str | None = None, **facts: Any) -> Move:
    def move(ctx: BrainContext) -> NextAction:
        arguments: dict[str, Any] = {"ref": ref(ctx, line_start)}
        if action:
            arguments |= {"action": action, "facts": facts}
        return NextAction(kind="tool", call=ToolCall(tool="browser_click", arguments=arguments))

    return move


def done(step: str, outcome: str = "ok") -> Move:
    return lambda ctx: NextAction(kind="step_done", step_id=step, reason=outcome)


def contract(*criteria: str) -> TaskContract:
    return TaskContract(
        outcome="test outcome",
        success_criteria=[Criterion(id=f"c{i}", text=t) for i, t in enumerate(criteria, 1)],
    )


def plan(*step_ids: str) -> Plan:
    return Plan(steps=[PlanStep(id=s, goal=f"do {s}", expected=f"{s} done") for s in step_ids])


class ScriptedBrain:
    def __init__(
        self,
        moves: list[Move],
        *,
        reads: list[ToolCall] | None = None,
        task: TaskContract | None = None,
        plans: list[Plan] | None = None,
    ) -> None:
        self.moves = list(moves)
        self.reads = reads or []
        self.task = task or contract("the thing is done")
        self.plans = plans or [plan("s1")]
        self.plan_calls = 0
        self.understand_calls = 0

    async def understand(self, ctx: BrainContext) -> UnderstandDecision:
        self.understand_calls += 1
        if self.reads and self.understand_calls == 1:
            return UnderstandDecision(kind="read", reads=self.reads)
        return UnderstandDecision(kind="contract", contract=self.task)

    async def plan(self, ctx: BrainContext) -> Plan:
        chosen = self.plans[min(self.plan_calls, len(self.plans) - 1)]
        self.plan_calls += 1
        return chosen.model_copy(deep=True)

    async def next_action(self, ctx: BrainContext) -> NextAction:
        if not self.moves:
            open_steps = ctx.state.plan.open_steps if ctx.state.plan else []
            return NextAction(
                kind="step_done", step_id=open_steps[0].id if open_steps else None, reason="script ended"
            )
        return self.moves.pop(0)(ctx)

    async def summarize(self, ctx: BrainContext) -> str:
        return f"scripted run with {len(ctx.state.observations)} observations"


class ScriptedVerifier:
    """Returns the given verdicts in order (the last one repeats)."""

    def __init__(self, *verdicts: bool | Callable[[RunState], bool]) -> None:
        self.verdicts = list(verdicts) or [True]
        self.calls = 0

    async def verify(self, state: RunState, tools: ToolContext) -> Verification:
        verdict = self.verdicts[min(self.calls, len(self.verdicts) - 1)]
        self.calls += 1
        passed = verdict(state) if callable(verdict) else verdict
        assert state.contract is not None
        return Verification(
            passed=passed,
            results=[
                CriterionResult(criterion_id=c.id, passed=passed, evidence="scripted")
                for c in state.contract.success_criteria
            ],
        )
