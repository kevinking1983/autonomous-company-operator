"""The human channel: how the operator asks a person a question or for approval.

Requests are recorded here and answered later. Pausing a run until the answer
arrives, and the approval inbox, are built on top of this (step 8).
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from company_operator.audit.log import EventLog
from company_operator.company_pack.models import Fact
from company_operator.policy.engine import Decision, PolicyEngine

Kind = Literal["clarification", "approval"]
Status = Literal["pending", "answered", "approved", "rejected"]


@dataclass
class HumanRequest:
    id: str
    kind: Kind
    question: str
    created_at: str
    audience: str  # "customer" for clarifications on a ticket, "supervisor" for approvals
    decision: Decision | None = None
    context: dict[str, Fact] = field(default_factory=dict)
    status: Status = "pending"
    response: str | None = None
    responder: str | None = None
    grant_id: str | None = None


class HumanChannel:
    def __init__(self, policy: PolicyEngine, log: EventLog) -> None:
        self.policy = policy
        self.log = log
        self._requests: dict[str, HumanRequest] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def _new(self, **kwargs: object) -> HumanRequest:
        with self._lock:
            request = HumanRequest(
                id=f"H{next(self._ids)}",
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
                **kwargs,  # type: ignore[arg-type]
            )
            self._requests[request.id] = request
        self.log.emit(
            "human.request",
            id=request.id,
            kind=request.kind,
            audience=request.audience,
            question=request.question,
            action=request.decision.action if request.decision else None,
            facts=request.decision.facts if request.decision else {},
            rules=request.decision.rules if request.decision else [],
        )
        return request

    def ask(self, question: str, audience: str = "supervisor") -> HumanRequest:
        return self._new(kind="clarification", question=question, audience=audience)

    def request_approval(self, decision: Decision, justification: str) -> HumanRequest:
        if decision.outcome != "needs_approval":
            raise ValueError("Approval is only requested for needs_approval decisions")
        return self._new(kind="approval", question=justification, audience="supervisor", decision=decision)

    def get(self, request_id: str) -> HumanRequest:
        return self._requests[request_id]

    @property
    def pending(self) -> list[HumanRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]

    def answer(self, request_id: str, response: str, responder: str) -> HumanRequest:
        request = self._requests[request_id]
        request.status, request.response, request.responder = "answered", response, responder
        self.log.emit("human.answered", id=request_id, response=response, responder=responder)
        return request

    def decide(self, request_id: str, approved: bool, approver: str, note: str = "") -> HumanRequest:
        """Record a supervisor's decision. Approval creates the grant the browser will need."""
        request = self._requests[request_id]
        if request.kind != "approval" or request.decision is None:
            raise ValueError(f"{request_id} is not an approval request")
        request.status = "approved" if approved else "rejected"
        request.response, request.responder = note, approver
        if approved:
            request.grant_id = self.policy.grant_approved(request.decision, request_id, approver).id
        self.log.emit("human.decided", id=request_id, approved=approved, approver=approver, note=note)
        return request
