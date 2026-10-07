"""Policy engine: decides whether an action may happen, and enforces that decision.

Two layers, both driven only by Company Pack data:

1. **Decisions.** Before acting, the operator declares the action it is about to
   take and the facts behind it (amount, payment id, customer history...). The
   engine answers allow, needs_approval (naming the rules that fired) or deny.
   An allow produces a *grant*.

2. **Enforcement.** The browser routes every non-GET request through the
   RequestGuard. A request is let through only if it is a sign-in, a read, or
   matches an action with a live grant whose facts agree with what is actually
   being submitted. Anything else is blocked before it leaves the browser.

So the operator cannot act without declaring, and cannot declare one thing
and then do another.
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.company_pack.models import Action, Fact, RequestPattern

Outcome = Literal["allow", "needs_approval", "deny"]
GRANT_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class Decision:
    action: str
    outcome: Outcome
    facts: dict[str, Fact]
    reasons: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


@dataclass
class Grant:
    id: str
    action: str
    facts: dict[str, Fact]
    source: str  # "policy" or "approval:<request id> by <approver>"
    uses_left: int | None  # None = unlimited until expiry
    expires_at: datetime

    @property
    def approved(self) -> bool:
        """Created by a person's approval, as opposed to issued by policy."""
        return self.source.startswith("approval:")

    @property
    def live(self) -> bool:
        return (self.uses_left is None or self.uses_left > 0) and datetime.now(UTC) < self.expires_at


@dataclass(frozen=True)
class GuardVerdict:
    allowed: bool
    action: str | None
    reason: str
    grant_id: str | None = None


def _same(a: Fact | str, b: Fact | str) -> bool:
    return str(a).strip().upper() == str(b).strip().upper()


def _bound_value_matches(submitted: str | None, fact: Fact, scale: float) -> bool:
    if submitted is None:
        return False
    try:
        return round(float(submitted), 2) == round(float(fact) * scale, 2)
    except (TypeError, ValueError):
        return _same(submitted, fact)


class PolicyEngine:
    def __init__(self, pack: CompanyPack, log: EventLog) -> None:
        self.pack = pack
        self.log = log
        self._grants: list[Grant] = []
        # Requests let through per action. Counted per action, not per grant: when several grants could
        # cover a request (say a person's approval and a policy grant for the same refund), the guard uses
        # whichever matches first.
        self.sent: dict[str, int] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # ── decisions ──

    def evaluate(self, action_id: str, facts: dict[str, Fact]) -> Decision:
        decision = self._decide(action_id, facts)
        self.log.emit(
            "policy.decision",
            action=action_id,
            outcome=decision.outcome,
            facts=facts,
            reasons=decision.reasons,
            rules=decision.rules,
        )
        return decision

    def _decide(self, action_id: str, facts: dict[str, Fact]) -> Decision:
        action = self.pack.actions.get(action_id)
        if action is None:
            return Decision(
                action_id, "deny", facts, [f"{action_id} is not an action {self.pack.company.name} defines"]
            )
        if not action.allowed:
            return Decision(
                action_id,
                "deny",
                facts,
                [f"{action_id} is not permitted for this role: {action.description}"],
            )
        if action.effect in ("money", "irreversible"):
            missing = sorted(self._required_facts(action) - facts.keys())
            if missing:
                return Decision(
                    action_id,
                    "deny",
                    facts,
                    [
                        f"{action.effect} actions must name exactly what they act on; missing facts: {', '.join(missing)}"
                    ],
                )
        rules = self.pack.approvals.triggered(action_id, {"action": action_id, **facts})
        if rules:
            return Decision(
                action_id,
                "needs_approval",
                facts,
                [r.description for r in rules],
                [r.id for r in rules],
            )
        return Decision(action_id, "allow", facts, ["within policy"])

    @staticmethod
    def _required_facts(action: Action) -> set[str]:
        required: set[str] = set()
        for pattern in action.requests:
            required.update(pattern.params)
            required.update(b.fact for b in pattern.bind)
        return required

    # ── grants ──

    def authorize(self, action_id: str, facts: dict[str, Fact]) -> tuple[Decision, Grant | None]:
        """Evaluate and, if allowed, issue a grant for the action."""
        decision = self.evaluate(action_id, facts)
        grant = self._issue(decision.action, facts, "policy") if decision.allowed else None
        return decision, grant

    def grant_approved(self, decision: Decision, approval_id: str, approver: str) -> Grant:
        """Turn a needs_approval decision into a grant once a human has approved it."""
        if decision.outcome != "needs_approval":
            raise ValueError(f"Only needs_approval decisions can be approved, not {decision.outcome}")
        return self._issue(decision.action, decision.facts, f"approval:{approval_id} by {approver}")

    def _issue(self, action_id: str, facts: dict[str, Fact], source: str) -> Grant:
        action = self.pack.action(action_id)
        grant = Grant(
            id=f"G{next(self._ids)}",
            action=action_id,
            facts=dict(facts),
            source=source,
            uses_left=1 if action.effect in ("money", "irreversible") else None,
            expires_at=datetime.now(UTC) + GRANT_TTL,
        )
        with self._lock:
            self._grants.append(grant)
        self.log.emit("policy.grant", grant=grant.id, action=action_id, facts=facts, source=source)
        return grant

    def release(self, grant_id: str) -> None:
        """Give back a use of a grant whose request the system refused (nothing was changed)."""
        with self._lock:
            for grant in self._grants:
                if grant.id == grant_id and grant.uses_left is not None:
                    grant.uses_left += 1
        self.log.emit("policy.grant_released", grant=grant_id)

    def find_grant(self, action_id: str, facts: dict[str, Fact]) -> Grant | None:
        """A live grant for exactly this action and these facts (e.g. one created by a human approval)."""
        with self._lock:
            for grant in self._grants:
                if (
                    grant.action == action_id
                    and grant.live
                    and all(k in grant.facts and _same(grant.facts[k], v) for k, v in facts.items())
                ):
                    return grant
        return None

    def revoke(self, grant_id: str) -> None:
        with self._lock:
            for grant in self._grants:
                if grant.id == grant_id:
                    grant.uses_left = 0

    def revoke_all(self) -> None:
        with self._lock:
            for grant in self._grants:
                grant.uses_left = 0

    # ── enforcement ──

    def guard(self, method: str, path: str, form: dict[str, str]) -> GuardVerdict:
        verdict = self._check(method.upper(), path, form)
        self.log.emit(
            "guard.allowed" if verdict.allowed else "guard.blocked",
            method=method.upper(),
            path=path,
            action=verdict.action,
            reason=verdict.reason,
            grant=verdict.grant_id,
        )
        return verdict

    def _check(self, method: str, path: str, form: dict[str, str]) -> GuardVerdict:
        if method in ("GET", "HEAD"):
            return GuardVerdict(True, None, "read request")
        for pattern in self.pack.session_requests:
            params = pattern.match(method, path, form)
            if params is not None and params.get("system") in self.pack.systems:
                return GuardVerdict(True, None, "sign-in/sign-out")

        matches = [
            (a, p, params)
            for a in self.pack.actions.values()
            for p in a.requests
            if (params := p.match(method, path, form)) is not None
        ]
        if not matches:
            return GuardVerdict(False, None, f"{method} {path} does not correspond to any permitted action")
        action, pattern, params = matches[0]
        if not action.allowed:
            return GuardVerdict(False, action.id, f"{action.id} is not permitted for this role")
        if action.effect == "read":
            return GuardVerdict(True, action.id, "read action")

        with self._lock:
            live = [g for g in self._grants if g.action == action.id and g.live]
            if not live:
                return GuardVerdict(
                    False, action.id, f"{action.id} was not authorised; declare it before submitting"
                )
            problems: list[str] = []
            for grant in live:
                mismatch = self._mismatch(grant, pattern, params, form)
                if mismatch is None:
                    if grant.uses_left is not None:
                        grant.uses_left -= 1
                    self.sent[action.id] = self.sent.get(action.id, 0) + 1
                    return GuardVerdict(
                        True, action.id, f"matches grant {grant.id} ({grant.source})", grant.id
                    )
                problems.append(f"{grant.id}: {mismatch}")
        return GuardVerdict(
            False, action.id, "submitted values differ from what was authorised: " + "; ".join(problems)
        )

    @staticmethod
    def _mismatch(
        grant: Grant, pattern: RequestPattern, params: dict[str, str], form: dict[str, str]
    ) -> str | None:
        for name, value in params.items():
            if name in grant.facts and not _same(grant.facts[name], value):
                return f"{name} is {value}, authorised {grant.facts[name]}"
        for bind in pattern.bind:
            if bind.fact in grant.facts and not _bound_value_matches(
                form.get(bind.field), grant.facts[bind.fact], bind.scale
            ):
                return f"{bind.field} is {form.get(bind.field)!r}, authorised {bind.fact}={grant.facts[bind.fact]}"
        return None
