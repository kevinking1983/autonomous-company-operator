"""Fault profiles: the ways real company systems misbehave, each one reproducible.

A profile is a configuration for the sandbox's fault switchboard
(sandbox/quickbite/faults.py), applied after the world is reset and before the
operator starts. Some faults only bite on certain kinds of work (a refund that
times out needs a refund), so a profile can name the case tags it applies to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FaultProfile:
    name: str
    label: str
    description: str
    faults: dict[str, Any] = field(default_factory=dict)
    applies_to: tuple[str, ...] = ()  # case tags; empty = every case

    def applies(self, tags: tuple[str, ...]) -> bool:
        return not self.applies_to or bool(set(tags) & set(self.applies_to))


PROFILES: dict[str, FaultProfile] = {
    p.name: p
    for p in [
        FaultProfile("clean", "Clean", "No faults: the systems behave."),
        FaultProfile(
            "flaky",
            "Flaky network",
            "15% of page requests fail with HTTP 500 (seeded, so the same requests fail every time).",
            {"error_rate": 0.15, "seed": 11},
        ),
        FaultProfile(
            "slow", "Slow systems", "Every request takes an extra 1.5 seconds.", {"latency_ms": 1500}
        ),
        FaultProfile(
            "session_expiry",
            "Sessions expire",
            "Every staff session is thrown away after 12 more requests, so the operator is signed out mid-task.",
            {"session_expiry_in": 12},
        ),
        FaultProfile(
            "stale_form",
            "Stale forms",
            "The first form submitted is rejected as expired, with nothing changed.",
            {"stale_form_next": 1},
        ),
        FaultProfile(
            "refund_timeout",
            "Refund times out",
            "The first refund commits, then the gateway answers 504: the money moved but the operator is not told.",
            {"refund_commit_timeout_next": 1},
            applies_to=("refund",),
        ),
        FaultProfile(
            "redesign",
            "UI redesign",
            "The main action buttons are renamed and moved, as after a redesign nobody announced.",
            {"layout": "shifted"},
        ),
        FaultProfile(
            "storm",
            "Everything at once",
            "Flaky network, expiring sessions, a stale form and the redesigned layout together.",
            {
                "error_rate": 0.1,
                "seed": 5,
                "session_expiry_in": 15,
                "stale_form_next": 1,
                "layout": "shifted",
            },
        ),
    ]
}

# Named sets of (case, profile) pairs. Runs cost model calls, so the suites are sized for a free quota.
SUITES: dict[str, dict[str, Any]] = {
    "smoke": {
        "label": "Smoke (3 runs)",
        "description": "Three quick cases with no faults: is everything wired up?",
        "pairs": [("missing_item", "clean"), ("late_delivery", "clean"), ("refund_status", "clean")],
    },
    "core": {
        "label": "Every scenario (14 runs)",
        "description": "All 14 ticket scenarios with no faults: does the operator do the right thing?",
        "pairs": [(case, "clean") for case in (
            "missing_item", "late_delivery", "wrong_order", "food_quality", "cancelled_but_charged",
            "cancelled_already_refunded", "double_charge", "refund_status", "cancel_order", "cancel_too_late",
            "rider_behaviour", "repeat_claimant", "duplicate_ticket", "vague",
        )],
    },
    "reliability": {
        "label": "Faults (12 runs)",
        "description": "Money-moving cases under every fault profile: does it stay correct when the systems misbehave?",
        "pairs": [
            ("missing_item", "flaky"), ("missing_item", "slow"), ("missing_item", "session_expiry"),
            ("missing_item", "stale_form"), ("missing_item", "refund_timeout"), ("missing_item", "redesign"),
            ("missing_item", "storm"), ("cancelled_but_charged", "refund_timeout"), ("late_delivery", "redesign"),
            ("late_delivery", "session_expiry"), ("rider_behaviour", "flaky"), ("cancel_order", "stale_form"),
        ],
    },
}  # fmt: skip
