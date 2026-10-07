"""Run state: everything the operator knows about one task, serialisable for checkpoints.

A run is a state machine over these phases:

    understand → plan → execute ⇄ observe → adapt → (execute | plan | escalate)
                                     ↓ all steps done
                                   verify → complete
                                     ↓ criteria failed
                                   adapt → plan

plus `awaiting_human` (paused for an answer or approval), which can follow any
phase. The terminal phases are `completed`, `escalated` and `failed`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Phase = Literal[
    "understand",
    "plan",
    "execute",
    "observe",
    "adapt",
    "verify",
    "complete",
    "awaiting_human",
    "completed",
    "escalated",
    "failed",
]
TERMINAL: frozenset[str] = frozenset({"completed", "escalated", "failed"})


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskRequest(Model):
    """What someone asked for, in their words."""

    text: str
    source: Literal["ticket", "supervisor", "api"] = "api"
    ticket_id: str | None = None
    requested_by: str = "unknown"


class Criterion(Model):
    id: str
    text: str


class TaskContract(Model):
    """The operator's interpretation of the request, written BEFORE acting.

    Verification later checks the success criteria against the systems of
    record, so they must be concrete and checkable.
    """

    outcome: str
    category: str | None = None
    sop_ids: list[str] = []
    success_criteria: list[Criterion]
    constraints: list[str] = []
    assumptions: list[str] = []


StepStatus = Literal["pending", "running", "done", "failed", "skipped"]


class PlanStep(Model):
    id: str
    goal: str
    expected: str  # postcondition that should hold when the step is done
    status: StepStatus = "pending"
    attempts: int = 0
    outcome: str = ""


class Plan(Model):
    version: int = 1
    rationale: str = ""
    steps: list[PlanStep]

    def step(self, step_id: str) -> PlanStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    @property
    def open_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status in ("pending", "running")]


class ToolCall(Model):
    tool: str
    arguments: dict[str, Any] = {}


class Observation(Model):
    """One action and what came of it."""

    seq: int
    phase: Phase
    step_id: str | None
    call: ToolCall
    ok: bool
    error: str | None = None
    output: str
    data: dict[str, Any] = {}
    evidence: list[str] = []
    images: list[str] = []
    at: str = Field(default_factory=now)


class CriterionResult(Model):
    criterion_id: str
    passed: bool
    evidence: str


class Verification(Model):
    passed: bool
    results: list[CriterionResult]
    at: str = Field(default_factory=now)

    @property
    def failed(self) -> list[CriterionResult]:
        return [r for r in self.results if not r.passed]


class Budgets(Model):
    max_decisions: int = 120  # every brain decision counts, including ones that call no tool
    max_repeats: int = 4  # the same decision this many times in a row means the brain is stuck
    max_tool_calls: int = 80
    max_attempts_per_step: int = 4
    max_replans: int = 3
    max_verify_rounds: int = 2
    max_understand_reads: int = 8


class Counters(Model):
    decisions: int = 0
    repeats: int = 0  # consecutive identical decisions
    tool_calls: int = 0
    replans: int = 0
    verify_rounds: int = 0
    understand_reads: int = 0


class RunState(Model):
    run_id: str
    request: TaskRequest
    phase: Phase = "understand"
    contract: TaskContract | None = None
    plan: Plan | None = None
    current_step: str | None = None
    observations: list[Observation] = []
    memory: dict[str, str] = {}  # facts discovered while working, e.g. payment_id
    hints: list[str] = []  # runtime guidance for the next decision (from Adapt)
    last_decision: str = ""  # fingerprint of the previous decision, for the stuck detector
    must_reobserve: bool = False  # set after an uncertain write: only reads allowed until state is re-read
    pending_human: str | None = None
    handover: str = ""  # set while handing the work over before escalating: the reason for stopping
    # Records a sub-task escalated to a person: this run must not change them (a person owns them now).
    hands_off: list[str] = []
    resume_phase: Phase | None = None
    verification: Verification | None = None
    summary: str = ""
    outcome_reason: str = ""
    budgets: Budgets = Budgets()
    counters: Counters = Counters()
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)

    @property
    def finished(self) -> bool:
        return self.phase in TERMINAL

    @property
    def last_observation(self) -> Observation | None:
        return self.observations[-1] if self.observations else None
