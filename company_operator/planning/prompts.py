"""Prompt construction. Everything company-specific comes from the Company Pack.

The system prompt is the operator's standing knowledge: who it is, how it must
work, the systems, the actions it may declare, the policies and the tone
guide. Each phase then gets a focused task prompt built from the run state:
the request, contract, plan, working memory, runtime hints, a compact action
history and the latest page in full.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from company_operator.company_pack import CompanyPack
from company_operator.company_pack.models import Action, CompanyFact, Sop
from company_operator.memory.store import Episode
from company_operator.runtime.brain import BrainContext
from company_operator.runtime.models import Observation, RunState

MAX_PAGES = 3
PAGE_BUDGET_CHARS = 30_000
HISTORY_LINES = 40

OPERATING_PRINCIPLES = """\
## How you work
- You do real work in the company's own systems through a real browser. Each page reaches you as a text
  snapshot; anything you can click or fill has a ref like [e12]. Refs change on every page load: only use
  refs from the LATEST snapshot.
- Establish facts in the systems of record before acting. Never act on a customer's description alone.
- The procedures (SOPs) and policies below are authoritative. Follow them; cite their numbers.
- To submit ANY change (reply, note, ticket update, refund, coupon, cancellation, incident, flag), call
  browser_click on the submit button with `action` (an action id from the list below) and `facts` holding the
  exact values the action needs (ids, amount in rupees as a number, reason...). Undeclared changes are
  blocked, and so is anything that differs from what you declared. Filling fields and opening pages needs
  no declaration.
- If an action needs approval, call request_approval with the same action and facts, then wait. Never try
  to work around a policy block.
- If a result is "uncertain", the change may already have happened: re-open the record and check before
  doing anything else. Never repeat a change you have not confirmed is missing.
- Be efficient: open pages directly when you can infer the path (system "ops", path "orders/QB-48213";
  system "payments", path "transactions?q=QB-48213"). Forms that span two pages (review, then confirm) need
  the fields filled on the first page only.
- Write to customers following the tone guide. Never reveal another customer's details.
- Before replying to a customer, make sure the thing you promise has actually happened (re-open the record).
- When only the customer can tell you something, reply on their ticket with ONE clear question (status
  pending_customer), then call wait_for_reply with the ticket id. You resume when they answer.
- A request that covers several tickets (e.g. "clear today's late-delivery tickets"): find exactly which
  tickets it means (read the queue), then call delegate_tasks with one task per ticket ("Resolve support
  ticket TKT-..."). Each is resolved and verified on its own; you resume with their outcomes. Your success
  criteria should name those tickets and what must be true of each. A sub-task that escalated has handed its
  ticket to a person: never act on that ticket yourself; report it as needing a person.
"""


def action_facts(action: Action) -> list[str]:
    facts: list[str] = []
    for pattern in action.requests:
        for name in [*pattern.params, *(b.fact for b in pattern.bind)]:
            if name not in facts:
                facts.append(name)
    return facts


def system_prompt(pack: CompanyPack, facts: list[CompanyFact] | None = None) -> str:
    c = pack.company
    systems = "\n".join(
        f"- {sid} ({s.name}, path prefix {s.base_path}): {s.purpose} Holds: {', '.join(s.holds)}."
        for sid, s in pack.systems.items()
    )
    actions = "\n".join(
        f"- {a.id} [{a.effect}]: {a.description}"
        + (f" Facts: {', '.join(action_facts(a))}." if action_facts(a) else "")
        for a in pack.actions.values()
        if a.allowed and a.effect != "read"
    )
    forbidden = "\n".join(f"- {f.rule}" for f in pack.forbidden)
    compensation = yaml.safe_dump(
        pack.compensation.model_dump(exclude_none=True), sort_keys=False, allow_unicode=True
    )
    approvals = "\n".join(
        f"- {r.id}: {r.description} (applies to {', '.join(r.applies_to)})" for r in pack.approvals.rules
    )
    facts_text = "\n".join(
        f"- {f.text}" + (" (learned from a supervisor)" if f.id.startswith("learned-") else "")
        for f in (pack.facts if facts is None else facts)
    )
    tone = pack.guides["tone"].body if "tone" in pack.guides else ""
    return f"""You are the {c.role.title} at {c.name}, working in the {c.role.team} team (reporting to {c.role.reports_to}).
{c.description.strip()}

Your mission: {c.role.mission.strip()}

{OPERATING_PRINCIPLES}
## Systems
{systems}

## Changes you may declare (browser_click `action`)
{actions}
Approval facts: when the policy below depends on them, also include customer_claims_30d, full_order_refund
and reason in `facts`, so the approval check sees the real situation.

## Never
{forbidden}

## Compensation policy (amounts in rupees)
{compensation}
## When a supervisor must approve
{approvals}

## Company facts (these override your own judgement)
{facts_text}

## Reply tone guide
{tone}
"""


# ───────────────────────── run-state rendering ─────────────────────────


def _compact_args(arguments: dict[str, Any]) -> str:
    shown = {k: v for k, v in arguments.items() if not k.startswith("_")}
    return json.dumps(shown, ensure_ascii=False)[:220]


def _headline(obs: Observation) -> str:
    for line in obs.output.splitlines():
        if line.startswith("Note: "):
            return line[6:]
    first = obs.output.splitlines()[0] if obs.output else ""
    if first.startswith("URL: "):
        title = next((ln[7:] for ln in obs.output.splitlines() if ln.startswith("Title: ")), "")
        return f"{first[5:]} ({title})"
    return first


def history(state: RunState) -> str:
    if not state.observations:
        return "(nothing yet)"
    lines = []
    for obs in state.observations[-HISTORY_LINES:]:
        status = "ok" if obs.ok else f"FAILED[{obs.error}]"
        step = f"/{obs.step_id}" if obs.step_id else ""
        lines.append(
            f"#{obs.seq} [{obs.phase}{step}] {obs.call.tool}({_compact_args(obs.call.arguments)}) → {status}: {_headline(obs)[:240]}"
        )
    return "\n".join(lines)


def recent_pages(state: RunState, max_pages: int = MAX_PAGES, budget: int = PAGE_BUDGET_CHARS) -> str:
    """The most recent distinct pages, newest first, in full (within a character budget).

    Several pages are shown, not just the last one, because a decision often
    needs facts from more than one system (the order AND the payment).
    """
    shown: list[str] = []
    seen: set[str] = set()
    used = 0
    for obs in reversed(state.observations):
        if not obs.output.startswith("URL: "):
            continue
        url = obs.output.splitlines()[0][5:]
        if url in seen:
            continue
        seen.add(url)
        text = obs.output
        room = budget - used
        if room < 1_500:
            break
        if len(text) > room:
            text = text[:room] + "\n... (page truncated)"
        label = "CURRENT PAGE (refs valid)" if not shown else "earlier page (refs no longer valid)"
        shown.append(f"### {label}, seen at #{obs.seq}\n{text}")
        used += len(text)
        if len(shown) >= max_pages:
            break
    return "\n\n".join(shown) if shown else "(no page open yet)"


def last_result(state: RunState) -> str:
    """The latest observation in full when it is not a page (e.g. a policy answer or approval decision)."""
    obs = state.last_observation
    if obs is None or obs.output.startswith("URL: "):
        return ""
    return f"\n## Result of your last action (#{obs.seq} {obs.call.tool})\n{obs.output}\n"


def request_block(state: RunState) -> str:
    r = state.request
    extra = f" (ticket {r.ticket_id})" if r.ticket_id else ""
    return f"## Request from {r.requested_by} via {r.source}{extra}\n{r.text}\n"


def contract_block(state: RunState) -> str:
    if not state.contract:
        return ""
    c = state.contract
    criteria = "\n".join(f"  - {cr.id}: {cr.text}" for cr in c.success_criteria)
    lines = [
        f"## Task contract\nOutcome: {c.outcome}",
        f"Category: {c.category}",
        f"Success criteria:\n{criteria}",
    ]
    if c.constraints:
        lines.append("Constraints: " + "; ".join(c.constraints))
    if c.assumptions:
        lines.append("Assumptions: " + "; ".join(c.assumptions))
    return "\n".join(lines) + "\n"


def plan_block(state: RunState) -> str:
    if not state.plan:
        return ""
    rows = []
    for s in state.plan.steps:
        marker = "→" if s.id == state.current_step else " "
        outcome = f" (outcome: {s.outcome})" if s.outcome else ""
        rows.append(f" {marker} [{s.status}] {s.id}: {s.goal}. Expected: {s.expected}{outcome}")
    return f"## Plan (version {state.plan.version})\n" + "\n".join(rows) + "\n"


def memory_block(state: RunState) -> str:
    if not state.memory:
        return ""
    return "## Working memory\n" + "\n".join(f"- {k}: {v}" for k, v in state.memory.items()) + "\n"


def hints_block(state: RunState) -> str:
    if not state.hints:
        return ""
    return "## Guidance from the runtime (follow it)\n" + "\n".join(f"- {h}" for h in state.hints[-4:]) + "\n"


def episodes_block(episodes: list[Episode]) -> str:
    if not episodes:
        return ""
    lines = []
    for e in episodes:
        actions = ", ".join(sorted({str(c["action"]) for c in e.changes})) or "no changes"
        lines.append(
            f"- [{e.outcome}{', verified' if e.verified else ''}] {e.request} → {actions}. {e.summary[:300]}"
        )
    return (
        "## How similar requests were handled before (for reference; this case may differ)\n"
        + "\n".join(lines)
        + "\n"
    )


def sops_block(sops: list[Sop]) -> str:
    return (
        "## Relevant procedures\n"
        + "\n\n".join(f"### SOP `{s.id}`: {s.title}\n{s.body}" for s in sops)
        + "\n"
    )


def sop_criteria_block(sops: list[Sop]) -> str:
    parts = []
    for s in sops:
        criteria = "\n".join(f"  - {c}" for c in s.success_criteria)
        parts.append(f"SOP `{s.id}` success criteria:\n{criteria}")
    return "\n".join(parts)


# ───────────────────────── phase prompts ─────────────────────────


def understand_prompt(ctx: BrainContext) -> str:
    s = ctx.state
    revising = (
        "\n## You are REVISING an earlier task contract\n" + contract_block(s) + last_result(s)
        if s.contract
        else ""
    )
    return f"""{request_block(s)}
{sops_block(ctx.sops)}
{sop_criteria_block(ctx.sops)}
{revising}
## What you have looked at so far
{history(s)}

## Pages you have read (most recent first; do not open these again)
{recent_pages(s, max_pages=5, budget=40_000)}
{hints_block(s)}
## Your job now: UNDERSTAND
Work out the outcome that is actually needed. First read the ticket AND the records the procedure says to
check (the order, the customer's claim history, the payments...): only reading is allowed in this phase, and
you may call several read tools at once. Decide the outcome only after applying the policies and the company
facts (including ones learned from supervisors) to what you found.
As soon as you know enough, call commit_contract (do not re-read pages shown above):
- outcome: one sentence describing the end state.
- category: the ticket category that fits.
- sop_ids: the SOPs that apply.
- success_criteria: concrete statements that can be CHECKED in the systems afterwards, with real ids and
  amounts (e.g. "A refund of ₹60.00 with reason missing_item exists on payment PAY-90012 (order QB-48213)").
  Adapt the SOP's criteria to this case. Include the customer reply and the final ticket status.
  If policy or a company fact rules something out (e.g. no refund for a repeat claimant), the criteria must
  say so ("No refund exists for QB-48275; the ticket is on_hold"). If the outcome depends on an approval you
  will request, write the criterion for both cases ("If approved, a refund ... exists; if rejected, none does
  and the ticket is on_hold").
- constraints and assumptions: anything you rely on but cannot confirm.
"""


def plan_prompt(ctx: BrainContext) -> str:
    s = ctx.state
    return f"""{request_block(s)}
{contract_block(s)}
{plan_block(s)}{memory_block(s)}
{sops_block(ctx.sops)}
{episodes_block(ctx.episodes)}
## What has happened so far
{history(s)}
{hints_block(s)}
## Your job now: PLAN
Call submit_plan with 2-7 ordered steps that will satisfy every success criterion. Each step needs an `id`
(short slug), a `goal`, and `expected`: what will be true and observable once the step is done.
{"Steps already done are kept; reuse their ids if they are still part of the plan." if s.plan else ""}
"""


def next_action_prompt(ctx: BrainContext) -> str:
    s = ctx.state
    return f"""{request_block(s)}
{contract_block(s)}
{plan_block(s)}{memory_block(s)}
{episodes_block(ctx.episodes)}
## Your recent actions
{history(s)}
{last_result(s)}
## Pages (most recent first)
{recent_pages(s)}
{hints_block(s)}
## Your job now: choose the ONE next action
- To act, call a tool. Always add `_step` (step id), `_expect` (what should be true afterwards) and
  `_why` (one short sentence). Use `_remember` to keep ids or amounts you will need later.
- Save turns: to complete a form, return several calls at once (browser_fill / browser_select for each
  field, then the browser_click that submits). They run in order; anything after the first call that is
  not a fill or select is dropped, because the page changes.
- Call step_done only when the CURRENT step's own work is done and its expected result is on screen.
- If the current step cannot be done as planned, call step_failed (or replan if the whole plan must change).
- If nothing more is needed for the whole task, call finish.

{current_step_block(s)}
"""


def current_step_block(state: RunState) -> str:
    if not state.plan or not state.current_step:
        return ""
    step = state.plan.step(state.current_step)
    if step is None:
        return ""
    done = [s.id for s in state.plan.steps if s.status == "done"]
    return (
        f"## >>> CURRENT STEP: `{step.id}` ({step.status}). {step.goal}\n"
        f"Done when: {step.expected}\n"
        f"Already finished (do not repeat): {', '.join(done) or 'none'}\n"
    )


def summary_prompt(ctx: BrainContext) -> str:
    s = ctx.state
    verification = ""
    if s.verification:
        verification = "\n## Verification\n" + "\n".join(
            f"- {r.criterion_id}: {'PASSED' if r.passed else 'FAILED'}, {r.evidence}"
            for r in s.verification.results
        )
    return f"""{request_block(s)}
{contract_block(s)}
{plan_block(s)}{memory_block(s)}
## What happened
{history(s)}
{verification}

Write a summary for your supervisor in 3-6 sentences: what was asked, what you found in the systems, what you
did (with record ids and amounts), and anything still pending. Plain text, no headings.
"""
