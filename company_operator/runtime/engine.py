"""The operator runtime: Goal → Understand → Plan → Execute → Observe → Adapt → Verify → Complete.

The runtime, not the model, owns:

* **the loop and its phases.** Every transition is logged and checkpointed.
* **budgets.** Tool calls, attempts per step, replans and verify rounds are
  all bounded. Running out escalates to a human instead of looping forever.
* **failure handling.** Adapt maps each failure kind from the tool layer to
  a fixed rule: retry, re-observe first, fix the input, request approval, or
  replan. Its guidance goes back to the brain as a hint.
* **safety after uncertainty.** After a write whose outcome is unknown, no
  new change may be declared until the operator has looked at the system
  again.
* **pausing for people.** When a tool opens a human request, the run stops in
  `awaiting_human` and resumes from its checkpoint once the request is
  answered.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.company_pack.models import CompanyFact, Sop
from company_operator.memory.store import Episode, OperatorDB
from company_operator.runtime.brain import Brain, BrainContext, NextAction, Verifier
from company_operator.runtime.models import Observation, Phase, Plan, PlanStep, RunState, ToolCall
from company_operator.runtime.store import RunStore
from company_operator.tools import ToolContext, ToolRegistry, ToolResult
from company_operator.tools.human import HumanRequest, reply_arrived

T = TypeVar("T")

# Tools that cannot change anything in the company's systems. (A click without a
# declared action is also read-only: the request guard blocks undeclared changes.)
READ_ONLY_TOOLS = frozenset(
    {"browser_open", "browser_snapshot", "browser_screenshot", "view_attachment", "check_policy"}
)
# Tools that only change form fields on the current page; they may be batched before a final call.
FIELD_TOOLS = frozenset({"browser_fill", "browser_select"})
BRAIN_RETRIES = 2
HANDOVER_DECISIONS = 12  # extra decisions and tool calls allowed for the handover
TRANSIENT_BACKOFF_SECONDS = 1.0


def is_read_only(call: ToolCall) -> bool:
    if call.tool in READ_ONLY_TOOLS:
        return True
    return call.tool == "browser_click" and not call.arguments.get("action")


def declares_change(call: ToolCall) -> bool:
    return call.tool == "browser_click" and bool(call.arguments.get("action"))


class Operator:
    def __init__(
        self,
        brain: Brain,
        verifier: Verifier,
        registry: ToolRegistry,
        pack: CompanyPack,
        store: RunStore,
        *,
        db: OperatorDB | None = None,
        transient_backoff: float = TRANSIENT_BACKOFF_SECONDS,
    ) -> None:
        self.db = db
        self.brain = brain
        self.verifier = verifier
        self.registry = registry
        self.pack = pack
        self.store = store
        self.transient_backoff = transient_backoff

    # ── public API ──

    async def run(self, state: RunState, tools: ToolContext) -> RunState:
        """Advance the run until it finishes or needs a person."""
        if not state.observations and state.phase == "understand":
            tools.log.emit("run.started", run_id=state.run_id, request=state.request.model_dump())
        while not state.finished and state.phase != "awaiting_human":
            await self.step(state, tools)
        if state.finished:
            self._remember_episode(state)
        return state

    async def resume(self, state: RunState, tools: ToolContext) -> RunState:
        """Continue a paused run if its human request has been answered."""
        if state.phase == "awaiting_human" and state.pending_human:
            request = tools.human.get(state.pending_human)
            if request.status == "pending" and request.kind == "external_reply":
                request = await self._check_for_reply(request, tools)
            if request.status == "pending":
                return state
            if request.status == "approved":
                tools.human.grant_for(request)  # the grant lives in this process's policy engine
            if request.kind == "external_reply":
                answer = f"A reply arrived on {request.context.get('record')}:\n{request.response}"
            else:
                answer = {
                    "approved": f"APPROVED by {request.responder}. You may now perform exactly the approved action.",
                    "rejected": f"REJECTED by {request.responder}: {request.response or 'no reason given'}. "
                    "Do not perform that action; continue without it or escalate.",
                    "answered": f"{request.responder} answered: {request.response}",
                }[request.status]
            self._observe(
                state,
                tools,
                ToolCall(tool="human_response", arguments={"request_id": request.id}),
                ToolResult.success(answer, data={"status": request.status}),
                phase="awaiting_human",
            )
            state.pending_human = None
            # An approval lets the plan continue. A rejection or an answer changes the situation, so the
            # task contract itself is revisited: what "done" means may no longer be what it was.
            revisit = request.status == "rejected" or request.kind in ("clarification", "external_reply")
            if revisit and state.contract:
                state.hints = [
                    "A person's response changed the situation (see the latest result). Revise the task contract: "
                    "success criteria must describe what should be true NOW, in light of that response."
                ]
                state.budgets.max_understand_reads = state.counters.understand_reads + 4
                self._enter(state, tools, "understand")
            else:
                self._enter(state, tools, state.resume_phase or "execute")
            state.resume_phase = None
            self.store.save(state)
        return await self.run(state, tools)

    async def step(self, state: RunState, tools: ToolContext) -> None:
        """Run one phase handler, then checkpoint."""
        handler = {
            "understand": self._understand,
            "plan": self._plan,
            "execute": self._execute,
            "observe": self._observe_last,
            "adapt": self._adapt,
            "verify": self._verify,
            "complete": self._complete,
        }[state.phase]
        try:
            await handler(state, tools)
        except BrainFailure as exc:
            state.outcome_reason = str(exc)
            self._enter(state, tools, "failed")
            tools.log.emit("run.failed", reason=str(exc))
        self.store.save(state)

    # ── phases ──

    async def _understand(self, state: RunState, tools: ToolContext) -> None:
        decision = await self._ask(state, tools, "understand", self.brain.understand)
        if decision.kind == "read":
            if not decision.reads:
                return self._escalate(state, tools, "Understand asked to read but named nothing to read.")
            for call in decision.reads:
                if not is_read_only(call):
                    state.hints.append(
                        f"During Understand only read-only tools are allowed; {call.tool} was refused."
                    )
                    tools.log.emit("understand.refused", tool=call.tool, arguments=call.arguments)
                    continue
                state.counters.understand_reads += 1
                if state.counters.understand_reads > state.budgets.max_understand_reads:
                    return self._escalate(
                        state, tools, "Could not establish what is being asked within the read budget."
                    )
                result = await self._call(state, tools, call)
                self._observe(state, tools, call, result, phase="understand")
            return None
        if decision.contract is None or not decision.contract.success_criteria:
            return self._escalate(state, tools, "Understand produced no checkable success criteria.")
        revised = state.contract is not None
        state.contract = decision.contract
        if revised:
            state.counters.verify_rounds = 0  # a new definition of done gets fresh verification rounds
        tools.log.emit(
            "contract.revised" if revised else "contract.created",
            contract=decision.contract.model_dump(),
            note=decision.note,
        )
        self._enter(state, tools, "plan")
        return None

    async def _plan(self, state: RunState, tools: ToolContext) -> None:
        plan = await self._ask(state, tools, "plan", self.brain.plan)
        ids = [s.id for s in plan.steps]
        if not plan.steps or len(set(ids)) != len(ids):
            state.hints.append("The plan must have at least one step and unique step ids.")
            state.counters.replans += 1
            if state.counters.replans > state.budgets.max_replans:
                return self._escalate(state, tools, "Could not produce a valid plan.")
            return None
        if state.plan:
            plan.version = state.plan.version + 1
            for step in plan.steps:  # keep the record of work already done
                old = state.plan.step(step.id)
                if old and old.status == "done":
                    step.status, step.outcome = "done", old.outcome
        state.plan = plan
        state.current_step = plan.open_steps[0].id if plan.open_steps else None
        state.hints.clear()
        tools.log.emit(
            "plan.created",
            version=plan.version,
            rationale=plan.rationale,
            steps=[s.model_dump() for s in plan.steps],
        )
        self._enter(state, tools, "execute")
        return None

    async def _execute(self, state: RunState, tools: ToolContext) -> None:
        assert state.plan is not None
        if not state.plan.open_steps:
            if state.handover:
                return self._finish_handover(state, tools)
            return self._enter(state, tools, "verify")
        state.counters.decisions += 1
        if state.counters.decisions > state.budgets.max_decisions:
            return self._escalate(
                state, tools, f"Decision budget of {state.budgets.max_decisions} exhausted."
            )
        action = await self._ask(state, tools, "next_action", self.brain.next_action)
        state.memory.update(action.remember)
        tools.log.emit("decision", **action.model_dump(exclude={"remember"}), remember=action.remember)
        # Stuck = the same decision again when nothing failed in between: repeating something that just
        # succeeded, or that changed nothing, is not progress. (Retrying after a failure is legitimate.)
        fingerprint = action.model_dump_json(include={"kind", "step_id", "call", "then", "reason"})
        last = state.last_observation
        retrying = last is not None and not last.ok
        state.counters.repeats = (
            state.counters.repeats + 1 if fingerprint == state.last_decision and not retrying else 1
        )
        state.last_decision = fingerprint
        if state.counters.repeats >= state.budgets.max_repeats:
            return self._escalate(
                state,
                tools,
                f"Stuck: made the same decision {state.counters.repeats} times in a row ({action.kind}).",
            )
        step = (
            state.plan.step(action.step_id) if action.step_id else state.plan.step(state.current_step or "")
        )

        match action.kind:
            case "tool":
                return await self._execute_tool(state, tools, action)
            case "step_done":
                if step is None or step.status not in ("pending", "running"):
                    current = state.plan.open_steps[0]
                    state.hints.append(
                        f"Step {action.step_id!r} is {'already ' + step.status if step else 'not in the plan'}. "
                        f"The current step is {current.id!r}: {current.goal}. Act on it."
                    )
                    tools.log.emit("decision.ignored", reason=state.hints[-1])
                    return None
                step.status, step.outcome = "done", action.reason
                tools.log.emit("step.done", step=step.id, outcome=action.reason)
                state.current_step = state.plan.open_steps[0].id if state.plan.open_steps else None
                state.hints.clear()
                return None
            case "step_failed":
                if step:
                    step.status, step.outcome = "failed", action.reason
                    tools.log.emit("step.failed", step=step.id, reason=action.reason)
                return self._replan(state, tools, f"Step {step.id if step else '?'} failed: {action.reason}")
            case "replan":
                return self._replan(state, tools, action.reason)
            case "finish":
                if state.handover:
                    return self._finish_handover(state, tools)
                for open_step in state.plan.open_steps:
                    open_step.status, open_step.outcome = "skipped", action.reason
                tools.log.emit("run.finish_requested", reason=action.reason)
                return self._enter(state, tools, "verify")
        return None

    async def _execute_tool(self, state: RunState, tools: ToolContext, action: NextAction) -> None:
        assert state.plan is not None and action.call is not None
        if action.step_id and state.plan.step(action.step_id):
            state.current_step = action.step_id
        step = state.plan.step(state.current_step or "")
        if step and step.status == "pending":
            step.status = "running"

        batch = self._batch(state, tools, [action.call, *action.then])
        for index, call in enumerate(batch):
            if state.must_reobserve and declares_change(call):
                refusal = ToolResult.failure(
                    "policy",
                    "Refused by the runtime: the previous change has an unknown outcome. Re-open the affected "
                    "record and check whether it took effect before declaring any new change.",
                )
                self._observe(state, tools, call, refusal, phase="execute")
                return self._enter(state, tools, "adapt")

            state.counters.tool_calls += 1
            if state.counters.tool_calls > state.budgets.max_tool_calls:
                return self._escalate(
                    state, tools, f"Tool-call budget of {state.budgets.max_tool_calls} exhausted."
                )
            result = await self._call(state, tools, call)
            if result.ok and is_read_only(call) and call.tool != "check_policy":
                state.must_reobserve = False
            last = index == len(batch) - 1
            self._observe(
                state, tools, call, result, phase="execute", expectation=action.expectation if last else ""
            )
            if not result.ok:
                break
        self._enter(state, tools, "observe")
        return None

    def _batch(self, state: RunState, tools: ToolContext, calls: list[ToolCall]) -> list[ToolCall]:
        """Keep field edits followed by at most one other call; anything after that would use stale refs."""
        for index, call in enumerate(calls):
            if call.tool not in FIELD_TOOLS:
                kept, dropped = calls[: index + 1], calls[index + 1 :]
                if dropped:
                    state.hints.append(
                        f"Only the first {len(kept)} of your {len(calls)} calls ran: after {call.tool} the page "
                        "may have changed, so look at the result before continuing."
                    )
                    tools.log.emit("batch.truncated", kept=len(kept), dropped=[c.tool for c in dropped])
                return kept
        return calls

    async def _observe_last(self, state: RunState, tools: ToolContext) -> None:
        obs = state.last_observation
        assert obs is not None
        if obs.data.get("pending") and obs.data.get("request_id"):
            state.pending_human = str(obs.data["request_id"])
            state.resume_phase = "execute"
            tools.log.emit("run.paused", waiting_for=state.pending_human)
            return self._enter(state, tools, "awaiting_human")
        return self._enter(state, tools, "execute" if obs.ok else "adapt")

    async def _adapt(self, state: RunState, tools: ToolContext) -> None:
        obs = state.last_observation
        assert obs is not None and state.plan is not None
        step = state.plan.step(state.current_step or "")
        if step:
            step.attempts += 1
            if step.attempts >= state.budgets.max_attempts_per_step:
                tools.log.emit("adapt.decision", rule="attempts_exhausted", step=step.id, error=obs.error)
                step.status, step.outcome = (
                    "failed",
                    f"gave up after {step.attempts} failed attempts ({obs.error})",
                )
                return self._replan(
                    state, tools, f"Step {step.id} kept failing ({obs.error}): {first_line(obs.output)}"
                )

        kind = obs.error or "unknown"
        rule, hint = ADAPT_RULES.get(kind, ADAPT_RULES["unknown"])
        if kind == "uncertain":
            state.must_reobserve = True
        if kind == "transient":
            await asyncio.sleep(self.transient_backoff)
        state.hints.append(hint.format(detail=first_line(obs.output, 300)))
        tools.log.emit(
            "adapt.decision", rule=rule, error=kind, step=step.id if step else None, hint=state.hints[-1]
        )
        self._enter(state, tools, "execute")
        return None

    async def _verify(self, state: RunState, tools: ToolContext) -> None:
        state.counters.verify_rounds += 1
        verification = await self.verifier.verify(state, tools)
        state.verification = verification
        tools.log.emit(
            "verify.result",
            passed=verification.passed,
            results=[r.model_dump() for r in verification.results],
        )
        if verification.passed:
            return self._enter(state, tools, "complete")
        if state.counters.verify_rounds >= state.budgets.max_verify_rounds:
            failed = "; ".join(r.criterion_id for r in verification.failed)
            return self._escalate(state, tools, f"Outcome could not be verified (failed: {failed}).")
        failures = "; ".join(f"{r.criterion_id}: {r.evidence}" for r in verification.failed)
        return self._replan(state, tools, f"Verification failed. Unmet criteria: {failures}")

    async def _complete(self, state: RunState, tools: ToolContext) -> None:
        state.summary = await self._ask(state, tools, "summarize", self.brain.summarize)
        self._enter(state, tools, "completed")
        tools.log.emit("run.completed", summary=state.summary)

    # ── helpers ──

    def _replan(self, state: RunState, tools: ToolContext, reason: str) -> None:
        if state.handover:  # no new plans while handing over
            return self._finish_handover(state, tools)
        state.counters.replans += 1
        if state.counters.replans > state.budgets.max_replans:
            return self._escalate(state, tools, f"Replan budget exhausted. Last reason: {reason}")
        state.hints.append(f"Replanning: {reason}")
        tools.log.emit("plan.replan", reason=reason, replans=state.counters.replans)
        return self._enter(state, tools, "plan")

    def _escalate(self, state: RunState, tools: ToolContext, reason: str) -> None:
        """Stop and hand over to a person, first leaving the work in a clean, explained state if possible."""
        handover = self.pack.company.role.handover.strip()
        if handover and not state.handover:
            state.handover = reason
            done = [s for s in state.plan.steps if s.status == "done"] if state.plan else []
            state.plan = Plan(
                version=(state.plan.version + 1) if state.plan else 1,
                rationale=f"Handing over: {reason}",
                steps=[
                    *done,
                    PlanStep(id="handover", goal=handover, expected="The work is handed over as described."),
                ],
            )
            state.current_step = "handover"
            state.counters.repeats = 0
            state.budgets.max_decisions = state.counters.decisions + HANDOVER_DECISIONS
            state.budgets.max_tool_calls = state.counters.tool_calls + HANDOVER_DECISIONS
            state.hints = [f"You are stopping because: {reason}. Do only the handover, then call step_done."]
            tools.log.emit("run.handover", reason=reason)
            return self._enter(state, tools, "execute")
        state.outcome_reason = state.handover or reason
        tools.log.emit("run.escalated", reason=state.outcome_reason)
        return self._enter(state, tools, "escalated")

    def _finish_handover(self, state: RunState, tools: ToolContext) -> None:
        state.outcome_reason = state.handover
        tools.log.emit("run.escalated", reason=state.handover)
        self._enter(state, tools, "escalated")

    async def _check_for_reply(self, request: HumanRequest, tools: ToolContext) -> HumanRequest:
        """Look at the record again: has something substantive appeared since the operator started waiting?"""
        url = request.context.get("url")
        if not url:
            return request
        page = await tools.browser.open(str(url))
        if page.error:
            return request
        reply = reply_arrived(str(request.context.get("baseline", "")), page.snapshot)
        if reply is None:
            return request
        return tools.human.answer(request.id, reply, responder=request.audience)

    def _remember_episode(self, state: RunState) -> None:
        if self.db is None:
            return
        changes = [
            {"action": o.call.arguments.get("action"), "facts": o.call.arguments.get("facts", {}), "ok": o.ok}
            for o in state.observations
            if declares_change(o.call) and o.ok and "authorisation was withdrawn" not in o.output
        ]
        self.db.add_episode(
            Episode(
                run_id=state.run_id,
                category=state.contract.category if state.contract else None,
                request=state.request.text,
                outcome=state.phase,
                summary=state.summary or state.outcome_reason,
                changes=changes,
                verified=bool(state.verification and state.verification.passed),
                created_at=state.updated_at,
            )
        )

    def _enter(self, state: RunState, tools: ToolContext, phase: Phase) -> None:
        if phase != state.phase:
            tools.log.emit("phase", **{"from": state.phase, "to": phase})
        state.phase = phase

    async def _call(self, state: RunState, tools: ToolContext, call: ToolCall) -> ToolResult:
        return await self.registry.call(tools, call.tool, call.arguments)

    def _observe(
        self,
        state: RunState,
        tools: ToolContext,
        call: ToolCall,
        result: ToolResult,
        *,
        phase: Phase,
        expectation: str = "",
    ) -> None:
        state.observations.append(
            Observation(
                seq=len(state.observations) + 1,
                phase=phase,
                step_id=state.current_step,
                call=call,
                ok=result.ok,
                error=result.error,
                output=result.output,
                data={**result.data, **({"expectation": expectation} if expectation else {})},
                evidence=[str(p) for p in result.evidence],
                images=[str(p) for p in result.images],
            )
        )

    def context(self, state: RunState, log: EventLog | None = None) -> BrainContext:
        return BrainContext(
            state=state,
            pack=self.pack,
            tools=self.registry.schemas(),
            sops=self._relevant_sops(state),
            facts=self._facts(),
            log=log,
            episodes=self._episodes(state),
        )

    def _facts(self) -> list[CompanyFact]:
        learned = self.db.facts() if self.db else []
        return [
            *self.pack.facts,
            *(CompanyFact(id=f"learned-{f.id}", text=f.text, source=f.source) for f in learned),
        ]

    def _episodes(self, state: RunState) -> list[Episode]:
        if self.db is None or not state.contract or not state.contract.category:
            return []
        return [e for e in self.db.episodes(state.contract.category) if e.run_id != state.run_id]

    def _relevant_sops(self, state: RunState) -> list[Sop]:
        if state.contract and state.contract.sop_ids:
            chosen = [self.pack.sops[i] for i in state.contract.sop_ids if i in self.pack.sops]
        else:
            chosen = self.pack.search_sops(state.request.text, limit=4)
        general = [s for s in self.pack.sops.values() if s.general and s not in chosen]
        return chosen + general

    async def _ask(
        self, state: RunState, tools: ToolContext, what: str, fn: Callable[[BrainContext], Awaitable[T]]
    ) -> T:
        """Call the brain, retrying a couple of times if it fails (e.g. malformed model output)."""
        last: Exception | None = None
        for attempt in range(1, BRAIN_RETRIES + 2):
            try:
                return await fn(self.context(state, tools.log))
            # Any brain failure (e.g. malformed model output) is retried, then fails the run cleanly.
            except Exception as exc:
                last = exc
                tools.log.emit(
                    "brain.error", call=what, attempt=attempt, error=f"{type(exc).__name__}: {exc}"
                )
        raise BrainFailure(f"The brain failed to {what} after {BRAIN_RETRIES + 1} attempts: {last}")


class BrainFailure(RuntimeError):
    pass


def first_line(text: str, limit: int = 200) -> str:
    """The most informative short line of a tool output (its Note if present)."""
    for line in text.splitlines():
        if line.startswith("Note: "):
            return line[6:][:limit]
    return (text.splitlines() or [""])[0][:limit]


# How Adapt responds to each failure kind: (rule name, hint for the brain).
ADAPT_RULES: dict[str, tuple[str, str]] = {
    "transient": (
        "retry_after_backoff",
        "The system kept failing even after automatic retries ({detail}). You may try once more, or take another route.",
    ),
    "uncertain": (
        "reobserve_before_retry",
        "A change was submitted but its outcome is unknown ({detail}). Re-open the affected record and check whether "
        "it took effect. Do NOT repeat the change unless you have confirmed it did not happen.",
    ),
    "rejected": (
        "fix_and_retry",
        "The system refused the request and nothing was saved ({detail}). Read the message, fix the input, and retry.",
    ),
    "not_found": (
        "reobserve",
        "Something was not where expected ({detail}). Take a fresh look at the page and use the current refs.",
    ),
    "invalid_input": ("fix_call", "The tool call was invalid ({detail}). Correct the arguments."),
    "policy": (
        "respect_policy",
        "Blocked by a company policy or runtime safety rule ({detail}). Do not try to work around it. Do what the "
        "message asks, choose a permitted alternative, or escalate.",
    ),
    "needs_approval": (
        "request_approval",
        "This needs supervisor approval ({detail}). Call request_approval with the same action and facts and a clear "
        "justification, then wait.",
    ),
    "unknown": (
        "reassess",
        "The last action did not succeed ({detail}). Reassess and choose the next action.",
    ),
}
