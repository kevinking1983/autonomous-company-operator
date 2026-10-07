"""The decision-making interface the runtime calls in its thinking phases.

The runtime owns the loop, the state, budgets, failure handling and
checkpoints. A Brain only answers four questions:

    understand   What is being asked, and what does "done" look like? (may read first)
    plan         What steps will get there, and what should be true after each?
    next_action  Given everything so far, what is the single next thing to do?
    summarize    How do we describe what happened?

The production brain is a language model (step 6). Tests use a scripted brain.
Both see the same BrainContext and return the same types, so the runtime
cannot tell them apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import Field

from company_operator.company_pack import CompanyPack
from company_operator.company_pack.models import CompanyFact, Sop
from company_operator.runtime.models import (
    Model,
    Observation,
    Plan,
    RunState,
    TaskContract,
    ToolCall,
    Verification,
)
from company_operator.tools import ToolContext


class UnderstandDecision(Model):
    """Either read more context first, or commit to a task contract."""

    kind: Literal["read", "contract"]
    reads: list[ToolCall] = Field(default_factory=list)
    contract: TaskContract | None = None
    note: str = ""


class NextAction(Model):
    kind: Literal["tool", "step_done", "step_failed", "replan", "finish"]
    step_id: str | None = None
    call: ToolCall | None = None
    expectation: str = ""  # what should be true after this call (checked in Observe)
    note: str = ""  # short reasoning, recorded in the audit log
    remember: dict[str, str] = Field(
        default_factory=dict
    )  # facts worth keeping, e.g. {"payment_id": "PAY-90012"}
    reason: str = ""  # for step_done / step_failed / replan / finish


@dataclass
class BrainContext:
    state: RunState
    pack: CompanyPack
    tools: list[dict[str, Any]]
    sops: list[Sop] = field(default_factory=list)
    facts: list[CompanyFact] = field(default_factory=list)

    def recent(self, n: int = 6) -> list[Observation]:
        return self.state.observations[-n:]


class Brain(Protocol):
    async def understand(self, ctx: BrainContext) -> UnderstandDecision: ...

    async def plan(self, ctx: BrainContext) -> Plan: ...

    async def next_action(self, ctx: BrainContext) -> NextAction: ...

    async def summarize(self, ctx: BrainContext) -> str: ...


class Verifier(Protocol):
    """Checks the task contract's success criteria against the systems of record (step 7)."""

    async def verify(self, state: RunState, tools: ToolContext) -> Verification: ...
