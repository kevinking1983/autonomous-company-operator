"""The production brain: a language model deciding through function calls.

Every phase is one model call that MUST answer with a function call:

    understand   read-only tools (look first) or commit_contract
    plan         submit_plan
    next_action  any operator tool, or step_done / step_failed / replan / finish
    summarize    plain text

Real tools get four optional meta-arguments (_step, _expect, _why,
_remember) so the model states which step it serves, what it expects to
see and why, without polluting the tools' own schemas. The meta-arguments
are stripped before the tool runs.

Each call is stateless: the prompt is rebuilt from the run state, so a run
can resume after a restart with nothing held in the model's memory.
"""

from __future__ import annotations

import contextvars
import copy
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from company_operator.audit.log import EventLog
from company_operator.llm.base import FunctionCall, ImagePart, LLMClient, LLMRequest, LLMResponse, ToolSpec
from company_operator.planning import prompts
from company_operator.runtime.brain import BrainContext, NextAction, UnderstandDecision
from company_operator.runtime.engine import is_read_only
from company_operator.runtime.models import Criterion, Plan, PlanStep, TaskContract, ToolCall

META_ARGS = {
    "_step": {"type": "string", "description": "Id of the plan step this action serves."},
    "_expect": {"type": "string", "description": "What should be true after this action."},
    "_why": {"type": "string", "description": "One short sentence: why this action."},
    "_remember": {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "description": 'Facts to keep for later steps, e.g. {"payment_id": "PAY-90012"}.',
    },
}

COMMIT_CONTRACT = ToolSpec(
    name="commit_contract",
    description="Commit to your interpretation of the task, with checkable success criteria. Ends the Understand phase.",
    parameters={
        "type": "object",
        "required": ["outcome", "success_criteria"],
        "properties": {
            "outcome": {"type": "string"},
            "category": {"type": "string"},
            "sop_ids": {"type": "array", "items": {"type": "string"}},
            "success_criteria": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
        },
    },
)

SUBMIT_PLAN = ToolSpec(
    name="submit_plan",
    description="Submit the ordered plan.",
    parameters={
        "type": "object",
        "required": ["steps"],
        "properties": {
            "rationale": {"type": "string"},
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["id", "goal", "expected"],
                    "properties": {
                        "id": {"type": "string"},
                        "goal": {"type": "string"},
                        "expected": {"type": "string"},
                    },
                },
            },
        },
    },
)

CONTROL_TOOLS = [
    ToolSpec(
        "step_done",
        "The CURRENT step's expected result now holds: you have done its work and seen the result on screen. "
        "Record what was achieved. (It always applies to the current step.)",
        {
            "type": "object",
            "required": ["outcome"],
            "properties": {"outcome": {"type": "string"}, "_remember": META_ARGS["_remember"]},
        },
    ),
    ToolSpec(
        "step_failed",
        "The current step cannot be completed as planned.",
        {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}}},
    ),
    ToolSpec(
        "replan",
        "The plan no longer fits what you found; make a new one.",
        {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}}},
    ),
    ToolSpec(
        "finish",
        "Nothing more needs doing for the whole task (remaining steps are unnecessary).",
        {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}}},
    ),
]

# The run's event log, so model calls (and the client's retries) land in the right run.
current_log: contextvars.ContextVar[EventLog | None] = contextvars.ContextVar("current_log", default=None)


def notify_current_run(event: str, data: dict[str, object]) -> None:
    log = current_log.get()
    if log is not None:
        log.emit(event, **data)


def with_meta(schema: dict[str, Any]) -> dict[str, Any]:
    params = copy.deepcopy(schema)
    params.setdefault("properties", {}).update(copy.deepcopy(META_ARGS))
    return params


def split_meta(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    real = {k: v for k, v in arguments.items() if not k.startswith("_")}
    meta = {k: v for k, v in arguments.items() if k.startswith("_")}
    return real, meta


def _strings(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v is not None}


class LLMBrain:
    def __init__(self, llm: LLMClient, max_images: int = 2) -> None:
        self.llm = llm
        self.max_images = max_images

    # ── phases ──

    async def understand(self, ctx: BrainContext) -> UnderstandDecision:
        read_tools = [t for t in self._tool_specs(ctx) if is_read_only(ToolCall(tool=t.name))]
        response = await self._call(
            ctx, "understand", prompts.understand_prompt(ctx), [*read_tools, COMMIT_CONTRACT], tool_mode="any"
        )
        commit = next((c for c in response.calls if c.name == "commit_contract"), None)
        if commit:
            a = commit.arguments
            criteria = [str(c).strip() for c in a.get("success_criteria") or [] if str(c).strip()]
            if not criteria:
                raise ValueError("commit_contract without success criteria")
            known = set(ctx.pack.sops)
            contract = TaskContract(
                outcome=str(a.get("outcome", "")).strip(),
                category=a.get("category") or None,
                sop_ids=[s for s in a.get("sop_ids") or [] if s in known],
                success_criteria=[Criterion(id=f"c{i}", text=t) for i, t in enumerate(criteria, 1)],
                constraints=[str(x) for x in a.get("constraints") or []],
                assumptions=[str(x) for x in a.get("assumptions") or []],
            )
            return UnderstandDecision(kind="contract", contract=contract)
        reads = [ToolCall(tool=c.name, arguments=split_meta(c.arguments)[0]) for c in response.calls]
        if not reads:
            raise ValueError("Understand answered without reading or committing")
        return UnderstandDecision(kind="read", reads=reads)

    async def plan(self, ctx: BrainContext) -> Plan:
        response = await self._call(ctx, "plan", prompts.plan_prompt(ctx), [SUBMIT_PLAN], tool_mode="any")
        call = self._first(response, {"submit_plan"})
        try:
            steps = [
                PlanStep(id=str(s["id"]), goal=str(s["goal"]), expected=str(s["expected"]))
                for s in call.arguments["steps"]
            ]
        except (KeyError, TypeError, ValidationError) as exc:
            raise ValueError(f"Malformed plan: {exc}") from exc
        return Plan(rationale=str(call.arguments.get("rationale", "")), steps=steps)

    async def next_action(self, ctx: BrainContext) -> NextAction:
        tools = [*self._tool_specs(ctx), *CONTROL_TOOLS]
        response = await self._call(
            ctx, "next_action", prompts.next_action_prompt(ctx), tools, tool_mode="any"
        )
        call = response.calls[0] if response.calls else None
        if call is None:
            raise ValueError("next_action answered without a function call")
        a = call.arguments
        match call.name:
            case "step_done":
                return NextAction(
                    kind="step_done",
                    step_id=ctx.state.current_step,
                    reason=str(a.get("outcome", "")),
                    remember=_strings(a.get("_remember")),
                )
            case "step_failed":
                return NextAction(
                    kind="step_failed", step_id=ctx.state.current_step, reason=str(a.get("reason", ""))
                )
            case "replan":
                return NextAction(kind="replan", reason=str(a.get("reason", "")))
            case "finish":
                return NextAction(kind="finish", reason=str(a.get("reason", "")))
        real, meta = split_meta(a)
        control = {t.name for t in CONTROL_TOOLS}
        follow_ups = [
            ToolCall(tool=c.name, arguments=split_meta(c.arguments)[0])
            for c in response.calls[1:]
            if c.name not in control
        ]
        expectation = next(
            (str(c.arguments["_expect"]) for c in reversed(response.calls) if c.arguments.get("_expect")), ""
        )
        return NextAction(
            kind="tool",
            step_id=meta.get("_step") or ctx.state.current_step,
            call=ToolCall(tool=call.name, arguments=real),
            then=follow_ups,
            expectation=expectation,
            note=str(meta.get("_why", "")),
            remember=_strings(meta.get("_remember")),
        )

    async def summarize(self, ctx: BrainContext) -> str:
        response = await self._call(ctx, "summarize", prompts.summary_prompt(ctx), [], tool_mode="none")
        return response.text.strip() or "(no summary)"

    # ── plumbing ──

    def _tool_specs(self, ctx: BrainContext) -> list[ToolSpec]:
        return [ToolSpec(t["name"], t["description"], with_meta(t["input_schema"])) for t in ctx.tools]

    def _images(self, ctx: BrainContext) -> list[ImagePart]:
        paths = [Path(p) for obs in ctx.state.observations for p in obs.images]
        return [ImagePart(p) for p in paths[-self.max_images :] if p.exists()]

    @staticmethod
    def _first(response: LLMResponse, names: set[str]) -> FunctionCall:
        for call in response.calls:
            if call.name in names:
                return call
        raise ValueError(
            f"Expected a call to {', '.join(sorted(names))}, got {[c.name for c in response.calls] or response.text[:120]!r}"
        )

    async def _call(
        self, ctx: BrainContext, purpose: str, prompt: str, tools: list[ToolSpec], *, tool_mode: str
    ) -> LLMResponse:
        request = LLMRequest(
            system=prompts.system_prompt(ctx.pack, ctx.facts),
            prompt=prompt,
            images=self._images(ctx),
            tools=tools,
            tool_mode=tool_mode,  # type: ignore[arg-type]
            purpose=purpose,
        )
        token = current_log.set(ctx.log)
        try:
            response = await self.llm.generate(request)
        finally:
            current_log.reset(token)
        if ctx.log is not None:
            ctx.log.emit(
                "llm.call",
                purpose=purpose,
                model=response.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_s=round(response.latency_s, 2),
                images=len(request.images),
                calls=[{"name": c.name, "arguments": c.arguments} for c in response.calls],
                text=response.text[:500],
            )
        return response
