"""The human channel: how the operator asks people for approval, answers, or replies.

Requests are stored durably (OperatorDB), so a run can pause for hours and be
resumed by another process. Three kinds of request:

* approval        a supervisor must approve a specific action with specific facts
* clarification   a question for the supervisor
* external_reply  waiting for someone outside (e.g. a customer) to answer on a
                  record; resolved when that record shows something new
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from company_operator.audit.log import EventLog
from company_operator.company_pack.models import Fact
from company_operator.memory.store import OperatorDB, StoredRequest
from company_operator.policy.engine import Decision, Grant, PolicyEngine

HumanRequest = StoredRequest


@dataclass
class HumanChannel:
    policy: PolicyEngine
    log: EventLog
    db: OperatorDB | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        if self.db is None:
            self.db = OperatorDB()  # in-memory: requests live as long as this channel

    @property
    def store(self) -> OperatorDB:
        assert self.db is not None
        return self.db

    # ── creating requests ──

    def _new(self, **fields: Any) -> HumanRequest:
        request = self.store.add_request(run_id=self.run_id, **fields)
        self.log.emit(
            "human.request",
            id=request.id,
            kind=request.kind,
            audience=request.audience,
            question=request.question,
            action=request.action,
            facts=request.facts,
            rules=request.rules,
        )
        return request

    def ask(
        self, question: str, audience: str = "supervisor", context: dict[str, Any] | None = None
    ) -> HumanRequest:
        return self._new(kind="clarification", audience=audience, question=question, context=context or {})

    def request_approval(
        self, decision: Decision, justification: str, context: dict[str, Any] | None = None
    ) -> HumanRequest:
        if decision.outcome != "needs_approval":
            raise ValueError("Approval is only requested for needs_approval decisions")
        return self._new(
            kind="approval",
            audience="supervisor",
            question=justification,
            action=decision.action,
            facts=dict(decision.facts),
            rules=list(decision.rules),
            reasons=list(decision.reasons),
            context=context or {},
        )

    def wait_for_tasks(self, about: str, task_ids: list[str]) -> HumanRequest:
        return self._new(kind="subtasks", audience="operator", question=about, context={"tasks": task_ids})

    def wait_for_reply(self, about: str, context: dict[str, Any]) -> HumanRequest:
        return self._new(kind="external_reply", audience="customer", question=about, context=context)

    # ── reading and resolving ──

    def get(self, request_id: str) -> HumanRequest:
        return self.store.get_request(request_id)

    @property
    def pending(self) -> list[HumanRequest]:
        return self.store.list_requests(status="pending", run_id=self.run_id)

    def answer(self, request_id: str, response: str, responder: str) -> HumanRequest:
        request = self.store.resolve_request(request_id, "answered", response, responder)
        self.log.emit("human.answered", id=request_id, response=response, responder=responder)
        return request

    def decide(
        self,
        request_id: str,
        approved: bool,
        approver: str,
        note: str = "",
        remember: bool = False,
        amount: float | None = None,
    ) -> HumanRequest:
        """Record a supervisor's decision. With remember=True the note becomes a learned company fact.

        `amount` approves a lower amount than was asked for. A supervisor may reduce what the operator
        does, never increase it: anything else would authorise something nobody has checked.
        """
        request = self.get(request_id)
        if request.kind != "approval":
            raise ValueError(f"{request_id} is not an approval request")
        if request.status != "pending":
            raise ValueError(f"{request_id} was already {request.status}")
        if amount is not None and approved:
            requested = request.facts.get("amount")
            if not isinstance(requested, int | float) or isinstance(requested, bool):
                raise ValueError(f"{request_id} has no amount to change")
            if not 0 < amount <= requested:
                raise ValueError(f"An approved amount must be above 0 and at most the requested {requested}")
            if amount < requested:
                facts = request.facts | {"amount": round(amount, 2)}
                if facts.get("full_order_refund") is True:
                    facts["full_order_refund"] = False  # part of the order is no longer the whole of it
                self.store.amend_request(request_id, facts, request.context | {"requested_amount": requested})
        request = self.store.resolve_request(
            request_id, "approved" if approved else "rejected", note, approver
        )
        if approved:
            self.grant_for(request)
        if remember and note.strip():
            verb = "approved" if approved else "rejected"
            self.store.add_fact(note, source=f"approval {request_id} ({request.action}) {verb} by {approver}")
        self.log.emit(
            "human.decided",
            id=request_id,
            approved=approved,
            approver=approver,
            note=note,
            remember=remember,
            facts=request.facts,
            requested_amount=request.context.get("requested_amount"),
        )
        return request

    def grant_for(self, request: HumanRequest) -> Grant:
        """The grant an approved request unlocks, in this run's policy engine (created if needed)."""
        assert request.status == "approved" and request.action
        facts: dict[str, Fact] = request.facts
        existing = self.policy.find_grant(request.action, facts)
        if existing:
            return existing
        decision = Decision(request.action, "needs_approval", facts, request.reasons, request.rules)
        return self.policy.grant_approved(decision, request.id, request.responder or "supervisor")


REF = re.compile(r"\s*\[e\d+[^\]]*\]")


def new_content(baseline: str, current: str) -> list[str]:
    """Lines on the record now that were not there when the operator started waiting (refs ignored)."""

    def norm(line: str) -> str:
        return REF.sub("", line).strip()

    before = {norm(line) for line in baseline.splitlines()}
    return [norm(line) for line in current.splitlines() if norm(line) and norm(line) not in before]


def reply_arrived(baseline: str, current: str) -> str | None:
    """The new content if something substantive appeared (a status flip alone is not a reply)."""
    added = new_content(baseline, current)
    if any(len(line) >= 30 for line in added):
        return "\n".join(added)
    return None
