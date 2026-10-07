"""Typed model of a Company Pack.

Everything the operator knows about how a company works is described by these
models. The runtime reads them; it never hard-codes a company's rules.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Effect = Literal["read", "write", "money", "irreversible"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ───────────────────────── company and systems ─────────────────────────


class Role(Model):
    title: str
    team: str
    reports_to: str
    mission: str
    escalation_contact: str


class Company(Model):
    name: str
    description: str
    currency: str
    timezone: str
    role: Role


class Credentials(Model):
    username: str
    password_env: str
    sandbox_default: str | None = None

    def password(self) -> str:
        """Read the password from the environment, falling back to the sandbox default."""
        value = os.environ.get(self.password_env) or self.sandbox_default
        if not value:
            raise LookupError(f"Set {self.password_env} to the password for {self.username}")
        return value


class System(Model):
    id: str
    name: str
    base_path: str
    purpose: str
    holds: list[str]
    credentials: Credentials

    def url(self, base_url: str) -> str:
        return base_url.rstrip("/") + self.base_path


# ───────────────────────── permissions ─────────────────────────


class Action(Model):
    id: str
    system: str
    effect: Effect
    description: str


class Forbidden(Model):
    id: str
    rule: str


# ───────────────────────── policies ─────────────────────────


class Amount(Model):
    basis: Literal["item_price", "order_total", "payment", "duplicate_payment"]
    percent: float = Field(gt=0, le=100)


class FollowUp(Model):
    action: str
    target: Literal["restaurant", "rider"]
    category: str


class CouponTier(Model):
    min_minutes_late: int = Field(ge=0)
    max_minutes_late: int | None = None
    coupon: int = Field(ge=0)  # rupees


class IssuePolicy(Model):
    remedy: Literal["refund", "coupon", "none"]
    amount: Amount | None = None
    tiers: list[CouponTier] | None = None
    requires_evidence: str | None = None
    follow_up: list[FollowUp] = []

    def coupon_for_delay(self, minutes_late: int) -> int:
        """Coupon value in rupees for a measured delivery delay."""
        for tier in self.tiers or []:
            upper = tier.max_minutes_late
            if minutes_late >= tier.min_minutes_late and (upper is None or minutes_late < upper):
                return tier.coupon
        return 0


class CompensationPolicy(Model):
    refund_settlement: str
    coupon_validity_days: int
    issues: dict[str, IssuePolicy]


Fact = str | int | float | bool


class Condition(Model):
    fact: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: Fact

    def matches(self, facts: dict[str, Fact]) -> bool:
        """True if the facts satisfy the condition. A fact that is not supplied never matches."""
        if self.fact not in facts:
            return False
        actual = facts[self.fact]
        match self.op:
            case "eq":
                return actual == self.value
            case "ne":
                return actual != self.value
        if isinstance(actual, bool) or not isinstance(actual, int | float):
            return False
        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            return False
        match self.op:
            case "gt":
                return actual > self.value
            case "gte":
                return actual >= self.value
            case "lt":
                return actual < self.value
            case _:
                return actual <= self.value


class ApprovalRule(Model):
    id: str
    description: str
    applies_to: list[str]
    when: Condition


class ApprovalPolicy(Model):
    approvers: list[str]
    rules: list[ApprovalRule]

    def triggered(self, action: str, facts: dict[str, Fact]) -> list[ApprovalRule]:
        """The approval rules a proposed action trips. Empty means no approval is needed."""
        return [r for r in self.rules if action in r.applies_to and r.when.matches(facts)]


# ───────────────────────── procedures and knowledge ─────────────────────────


class Sop(Model):
    id: str
    title: str
    applies_to: list[str]
    keywords: list[str]
    systems: list[str]
    actions: list[str]
    success_criteria: list[str]
    body: str
    path: Path

    @property
    def general(self) -> bool:
        return "all" in self.applies_to


class Guide(Model):
    id: str
    title: str
    body: str


class CompanyFact(Model):
    id: str
    text: str
    source: str


class CompanyPack(Model):
    id: str
    root: Path
    company: Company
    systems: dict[str, System]
    actions: dict[str, Action]
    forbidden: list[Forbidden]
    compensation: CompensationPolicy
    approvals: ApprovalPolicy
    sops: dict[str, Sop]
    guides: dict[str, Guide]
    facts: list[CompanyFact]

    def action(self, action_id: str) -> Action:
        try:
            return self.actions[action_id]
        except KeyError:
            raise KeyError(f"{self.company.name} has no action {action_id!r}") from None

    def sops_for(self, category: str) -> list[Sop]:
        """SOPs that apply to a ticket category: the specific ones first, then the general ones."""
        specific = [s for s in self.sops.values() if category in s.applies_to]
        general = [s for s in self.sops.values() if s.general]
        return specific + general

    def search_sops(self, text: str, limit: int = 3) -> list[Sop]:
        """Rank SOPs by how many of their keywords appear in free text (e.g. a ticket).

        A cheap first filter for the Understand phase; the model makes the final call.
        """
        haystack = text.lower()
        scored = []
        for sop in self.sops.values():
            score = sum(1 for k in sop.keywords if re.search(rf"\b{re.escape(k.lower())}\b", haystack))
            if score:
                scored.append((score, sop.id, sop))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [sop for _, _, sop in scored[:limit]]
