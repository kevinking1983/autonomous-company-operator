"""Ground truth: the sandbox's own records before and after a run.

The control API exposes raw table rows to the harness only (never to the
operator). A check compares the two snapshots, so rows that existed before the
run (an earlier refund, an automatic one) are never credited to the operator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

Row = dict[str, Any]

# Table → the column that identifies a row.
TABLES: dict[str, str] = {
    "pay_payments": "id",
    "pay_refunds": "id",
    "pay_coupons": "code",
    "ops_orders": "id",
    "ops_incidents": "id",
    "ops_restaurants": "id",
    "support_tickets": "id",
    "support_messages": "id",
}


class Sandbox:
    """The harness's handle on the sandbox: reset it, inject faults, read its records."""

    def __init__(self, url: str, control_key: str, timeout: float = 30.0) -> None:
        self.url = url
        self.http = httpx.Client(base_url=url, headers={"X-Control-Key": control_key}, timeout=timeout)

    def reset(self) -> None:
        self.http.post("/_control/reset").raise_for_status()

    def set_faults(self, config: dict[str, Any]) -> None:
        self.http.put("/_control/faults", json=config).raise_for_status()

    def faults(self) -> dict[str, Any]:
        response = self.http.get("/_control/faults")
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    def snapshot(self) -> dict[str, list[Row]]:
        snapshot: dict[str, list[Row]] = {}
        for table in TABLES:
            response = self.http.get(f"/_control/state/{table}")
            response.raise_for_status()
            snapshot[table] = response.json()
        return snapshot


@dataclass
class World:
    """What the sandbox held before the run and after it."""

    before: dict[str, list[Row]]
    after: dict[str, list[Row]]
    _index: dict[tuple[str, str], dict[str, Row]] = field(default_factory=dict, repr=False)

    def _keyed(self, which: str, table: str) -> dict[str, Row]:
        if (which, table) not in self._index:
            rows = self.before[table] if which == "before" else self.after[table]
            self._index[(which, table)] = {str(r[TABLES[table]]): r for r in rows}
        return self._index[(which, table)]

    def new(self, table: str, **match: Any) -> list[Row]:
        """Rows created during the run, optionally filtered by column values."""
        before = self._keyed("before", table)
        return [
            r
            for key, r in self._keyed("after", table).items()
            if key not in before and all(r.get(k) == v for k, v in match.items())
        ]

    def get(self, table: str, key: str, when: str = "after") -> Row | None:
        return self._keyed(when, table).get(key)

    # ── shortcuts the checks use ──

    def refunds(self, order_id: str) -> list[Row]:
        return self.new("pay_refunds", order_id=order_id)

    def coupons(self, customer_id: str) -> list[Row]:
        return self.new("pay_coupons", customer_id=customer_id)

    def payments(self, order_id: str) -> list[Row]:
        return sorted(
            (r for r in self.after["pay_payments"] if r["order_id"] == order_id),
            key=lambda r: r["captured_at"],
        )

    def money_moved(self) -> list[Row]:
        """Every refund and coupon created during the run, anywhere."""
        return self.new("pay_refunds") + self.new("pay_coupons")

    def ticket(self, ticket_id: str) -> Row:
        ticket = self.get("support_tickets", ticket_id)
        assert ticket is not None, f"no ticket {ticket_id}"
        return ticket

    def replies(self, ticket_id: str) -> list[Row]:
        """New messages to the customer (not internal notes) on a ticket."""
        return [
            m
            for m in self.new("support_messages", ticket_id=ticket_id)
            if m["author_type"] == "agent" and not m["internal"]
        ]

    def notes(self, ticket_id: str) -> list[Row]:
        return [m for m in self.new("support_messages", ticket_id=ticket_id) if m["internal"]]
