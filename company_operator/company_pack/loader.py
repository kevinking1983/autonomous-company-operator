"""Load a Company Pack from disk and cross-check it.

A pack is only useful if it is internally consistent: an SOP that names an
action the company never granted, or an approval rule about a fact nobody
supplies, would silently do nothing. So loading fails fast, listing every
problem at once.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from company_operator.company_pack.models import (
    Action,
    ApprovalPolicy,
    Company,
    CompanyFact,
    CompanyPack,
    CompensationPolicy,
    Forbidden,
    Guide,
    RequestPattern,
    Sop,
    System,
)

# Facts the operator can supply when asking whether an action needs approval.
KNOWN_APPROVAL_FACTS = frozenset(
    {"action", "amount", "reason", "full_order_refund", "customer_claims_30d", "batch_size", "off_policy"}
)


class PackError(ValueError):
    def __init__(self, root: Path, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"Invalid company pack at {root}:\n  - " + "\n  - ".join(problems))


def _yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def split_front_matter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    """Split a Markdown file into its YAML header and body."""
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: missing YAML front matter")
    _, header, body = text.split("---\n", 2)
    meta = yaml.safe_load(header)
    if not isinstance(meta, dict):
        raise ValueError(f"{path.name}: front matter must be a mapping")
    return meta, body.strip()


def load_pack(root: Path) -> CompanyPack:
    root = root.resolve()
    if not root.is_dir():
        raise PackError(root, ["directory does not exist"])
    try:
        pack = _parse(root)
    except (ValidationError, ValueError, OSError, yaml.YAMLError) as exc:
        raise PackError(root, [str(exc)]) from exc
    problems = validate(pack)
    if problems:
        raise PackError(root, problems)
    return pack


def _parse(root: Path) -> CompanyPack:
    systems = {sid: System(id=sid, **spec) for sid, spec in _yaml(root / "systems.yaml")["systems"].items()}
    permissions = _yaml(root / "permissions.yaml")

    actions: dict[str, Action] = {}
    for spec in permissions["actions"]:
        action = Action(**spec)
        if action.id in actions:
            raise ValueError(f"permissions.yaml: duplicate action {action.id}")
        actions[action.id] = action

    sops: dict[str, Sop] = {}
    for path in sorted((root / "sops").glob("*.md")):
        meta, body = split_front_matter(path.read_text(encoding="utf-8"), path)
        sop = Sop(**meta, body=body, path=path)
        if sop.id in sops:
            raise ValueError(f"{path.name}: duplicate SOP id {sop.id}")
        sops[sop.id] = sop

    guides = {}
    for path in sorted((root / "guides").glob("*.md")):
        meta, body = split_front_matter(path.read_text(encoding="utf-8"), path)
        guides[meta["id"]] = Guide(**meta, body=body)

    return CompanyPack(
        id=root.name,
        root=root,
        company=Company(**_yaml(root / "company.yaml")),
        systems=systems,
        actions=actions,
        session_requests=[RequestPattern(**r) for r in permissions.get("session_requests", [])],
        forbidden=[Forbidden(**f) for f in permissions.get("forbidden", [])],
        compensation=CompensationPolicy(**_yaml(root / "policies" / "compensation.yaml")),
        approvals=ApprovalPolicy(**_yaml(root / "policies" / "approvals.yaml")),
        sops=sops,
        guides=guides,
        facts=[CompanyFact(**f) for f in _yaml(root / "facts.yaml")["facts"]],
    )


def validate(pack: CompanyPack) -> list[str]:
    """Cross-reference checks that per-file schemas cannot express."""
    problems: list[str] = []

    for action in pack.actions.values():
        if action.system not in pack.systems:
            problems.append(f"action {action.id}: unknown system {action.system!r}")
        if not action.id.startswith(f"{action.system}."):
            problems.append(f"action {action.id}: id must start with its system name")
        if action.effect != "read" and not action.requests:
            problems.append(
                f"action {action.id}: a {action.effect} action needs at least one request pattern"
            )
        system = pack.systems.get(action.system)
        for pattern in action.requests:
            if system and not pattern.path.startswith(system.base_path):
                problems.append(
                    f"action {action.id}: request path {pattern.path} is outside {system.base_path}"
                )

    for sop in pack.sops.values():
        problems += [f"SOP {sop.id}: unknown system {s!r}" for s in sop.systems if s not in pack.systems]
        problems += [f"SOP {sop.id}: unknown action {a!r}" for a in sop.actions if a not in pack.actions]
        if not sop.success_criteria:
            problems.append(f"SOP {sop.id}: needs at least one success criterion")

    for name, issue in pack.compensation.issues.items():
        if issue.remedy == "refund" and issue.amount is None:
            problems.append(f"compensation {name}: a refund remedy needs an amount")
        if issue.remedy == "coupon":
            problems += _check_tiers(name, issue.tiers or [])
        for follow in issue.follow_up:
            if follow.action not in pack.actions:
                problems.append(f"compensation {name}: unknown follow-up action {follow.action!r}")

    seen_rules: set[str] = set()
    for rule in pack.approvals.rules:
        if rule.id in seen_rules:
            problems.append(f"approval rule {rule.id}: duplicate id")
        seen_rules.add(rule.id)
        problems += [
            f"approval rule {rule.id}: unknown action {a!r}" for a in rule.applies_to if a not in pack.actions
        ]
        if rule.when.fact not in KNOWN_APPROVAL_FACTS:
            problems.append(f"approval rule {rule.id}: unknown fact {rule.when.fact!r}")

    return problems


def _check_tiers(name: str, tiers: list[Any]) -> list[str]:
    if not tiers:
        return [f"compensation {name}: a coupon remedy needs tiers"]
    problems = []
    if tiers[0].min_minutes_late != 0:
        problems.append(f"compensation {name}: tiers must start at 0 minutes")
    for lower, upper in pairwise(tiers):
        if lower.max_minutes_late != upper.min_minutes_late:
            problems.append(
                f"compensation {name}: tiers must be contiguous (gap at {lower.max_minutes_late})"
            )
    if tiers[-1].max_minutes_late is not None:
        problems.append(f"compensation {name}: the last tier must be open-ended")
    return problems
