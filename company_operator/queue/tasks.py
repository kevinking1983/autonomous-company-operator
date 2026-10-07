"""The operator's work queue: durable, prioritised, safe with several workers and crashes.

A task is a unit of work ("resolve TKT-1001", "clear today's late-delivery
tickets"). Its run, with checkpoint, audit log and report, is linked once a
worker starts it.

    queued ──claim──▶ running ──▶ completed / escalated / failed
                         │  ▲
          paused for a   ▼  │ due for a check (an answer may have arrived)
          person ──▶ waiting

A running task holds a lease that its worker renews. If the worker dies, the
lease expires and another worker reclaims the task, which resumes from its
checkpoint.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from company_operator.memory.store import OperatorDB, now

ACTIVE = ("queued", "running", "waiting")
TERMINAL = ("completed", "escalated", "failed", "cancelled")
PRIORITY = {"low": 0, "normal": 1, "high": 2, "urgent": 3}


def _at(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat(timespec="seconds")


@dataclass
class Task:
    id: str
    text: str
    ticket_id: str | None
    source: str
    requested_by: str
    priority: int
    status: str
    parent_id: str | None
    run_id: str | None
    worker: str | None
    lease_until: str | None
    next_check_at: str | None
    attempts: int
    error: str | None
    created_at: str
    updated_at: str

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL


def _task(row: sqlite3.Row) -> Task:
    data: dict[str, Any] = dict(row)
    data.pop("seq")
    return Task(**data)


class TaskQueue:
    def __init__(self, db: OperatorDB, lease: timedelta = timedelta(minutes=2)) -> None:
        self.db = db
        self.lease = lease

    def enqueue(
        self,
        text: str,
        *,
        ticket_id: str | None = None,
        source: str = "api",
        requested_by: str = "unknown",
        priority: int = PRIORITY["normal"],
        parent_id: str | None = None,
    ) -> Task:
        """Add a task. A ticket that already has an active task is not queued twice."""
        with self.db.transaction() as conn:
            if ticket_id:
                existing = conn.execute(
                    f"SELECT * FROM tasks WHERE ticket_id = ? AND status IN ({','.join('?' * len(ACTIVE))})",
                    (ticket_id, *ACTIVE),
                ).fetchone()
                if existing:
                    return _task(existing)
            seq = (conn.execute("SELECT MAX(seq) FROM tasks").fetchone()[0] or 0) + 1
            stamp = now()
            conn.execute(
                """INSERT INTO tasks (id, seq, text, ticket_id, source, requested_by, priority, status, parent_id,
                     created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)""",
                (f"T-{seq}", seq, text, ticket_id, source, requested_by, priority, parent_id, stamp, stamp),
            )
            return _task(conn.execute("SELECT * FROM tasks WHERE id = ?", (f"T-{seq}",)).fetchone())

    def claim(self, worker: str) -> Task | None:
        """Atomically take the next task to work on: queued, due for a check, or abandoned by a dead worker."""
        stamp = now()
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT id FROM tasks
                   WHERE status = 'queued'
                      OR (status = 'waiting' AND next_check_at <= ?)
                      OR (status = 'running' AND lease_until < ?)
                   -- new work before re-checking paused tasks, or a waiting parent starves its own sub-tasks
                   ORDER BY priority DESC, CASE status WHEN 'waiting' THEN 1 ELSE 0 END, seq LIMIT 1""",
                (stamp, stamp),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE tasks SET status = 'running', worker = ?, lease_until = ?, attempts = attempts + 1,
                     updated_at = ? WHERE id = ?""",
                (worker, _at(self.lease), stamp, row["id"]),
            )
            return _task(conn.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone())

    def renew(self, task_id: str, worker: str) -> None:
        self.db._exec(
            "UPDATE tasks SET lease_until = ? WHERE id = ? AND worker = ? AND status = 'running'",
            _at(self.lease),
            task_id,
            worker,
        )

    def attach_run(self, task_id: str, run_id: str) -> None:
        self.db._exec("UPDATE tasks SET run_id = ?, updated_at = ? WHERE id = ?", run_id, now(), task_id)

    def finish(self, task_id: str, status: str, error: str | None = None) -> None:
        assert status in TERMINAL
        self.db._exec(
            "UPDATE tasks SET status = ?, error = ?, worker = NULL, lease_until = NULL, updated_at = ? WHERE id = ?",
            status,
            error,
            now(),
            task_id,
        )

    def wait(self, task_id: str, check_in: timedelta) -> None:
        """The run is paused for someone; look at it again after `check_in`."""
        self.db._exec(
            """UPDATE tasks SET status = 'waiting', worker = NULL, lease_until = NULL, next_check_at = ?,
                 updated_at = ? WHERE id = ?""",
            _at(check_in),
            now(),
            task_id,
        )

    def cancel(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task.status in ("queued", "waiting"):
            self.finish(task_id, "cancelled")
        return self.get(task_id)

    def get(self, task_id: str) -> Task:
        rows = self.db._exec("SELECT * FROM tasks WHERE id = ?", task_id)
        if not rows:
            raise KeyError(f"No task {task_id}")
        return _task(rows[0])

    def list(self, status: str | None = None, parent_id: str | None = None, limit: int = 200) -> list[Task]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if parent_id:
            clauses.append("parent_id = ?")
            params.append(parent_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db._exec(f"SELECT * FROM tasks {where} ORDER BY seq DESC LIMIT ?", *params, limit)
        return [_task(r) for r in rows]

    def counts(self) -> dict[str, int]:
        return {
            r["status"]: r["n"]
            for r in self.db._exec("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
        }
