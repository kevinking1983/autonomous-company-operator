"""The operator's durable store: requests to people, learned company facts, and past episodes.

One SQLite file (data/operator.db) that outlives any single run or process:

* **human_requests**: approvals, questions and waits for an external reply.
  A run can pause for hours, and the request (with everything needed to
  rebuild the approved grant) survives restarts.
* **learned_facts**: rules people taught the operator, e.g. the reason
  given when an approval is rejected. Future runs see them next to the
  Company Pack's own facts.
* **episodes**: one short record per finished run: what kind of request,
  what was done, whether it verified. Similar future requests are shown how
  earlier ones were handled.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS human_requests (
    id          TEXT PRIMARY KEY,
    seq         INTEGER NOT NULL,
    run_id      TEXT,
    kind        TEXT NOT NULL,      -- approval / clarification / external_reply
    audience    TEXT NOT NULL,      -- supervisor / customer
    question    TEXT NOT NULL,
    action      TEXT,
    facts       TEXT NOT NULL DEFAULT '{}',
    rules       TEXT NOT NULL DEFAULT '[]',
    reasons     TEXT NOT NULL DEFAULT '[]',
    context     TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL,      -- pending / approved / rejected / answered
    response    TEXT,
    responder   TEXT,
    created_at  TEXT NOT NULL,
    decided_at  TEXT
);
CREATE TABLE IF NOT EXISTS learned_facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    source      TEXT NOT NULL,      -- e.g. "approval H-1003 rejected by priya.supervisor"
    created_at  TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    seq           INTEGER NOT NULL,
    text          TEXT NOT NULL,
    ticket_id     TEXT,
    source        TEXT NOT NULL,      -- ticket / supervisor / api
    requested_by  TEXT NOT NULL,
    priority      INTEGER NOT NULL,   -- higher runs first
    status        TEXT NOT NULL,      -- queued / running / waiting / completed / escalated / failed / cancelled
    parent_id     TEXT,
    run_id        TEXT,
    worker        TEXT,
    lease_until   TEXT,               -- a running task whose lease expired is reclaimed (worker crashed)
    next_check_at TEXT,               -- when a waiting task should be looked at again
    attempts      INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    run_id      TEXT PRIMARY KEY,
    category    TEXT,
    request     TEXT NOT NULL,
    outcome     TEXT NOT NULL,      -- completed / escalated / failed
    summary     TEXT NOT NULL,
    changes     TEXT NOT NULL DEFAULT '[]',
    verified    INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
"""

FIRST_REQUEST_SEQ = 1001


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class StoredRequest:
    id: str
    run_id: str | None
    kind: str
    audience: str
    question: str
    status: str
    created_at: str
    action: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    rules: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    response: str | None = None
    responder: str | None = None
    decided_at: str | None = None


@dataclass
class LearnedFact:
    id: int
    text: str
    source: str
    created_at: str


@dataclass
class Episode:
    run_id: str
    category: str | None
    request: str
    outcome: str
    summary: str
    changes: list[dict[str, Any]]
    verified: bool
    created_at: str


class OperatorDB:
    """Thread-safe SQLite store. `path=None` keeps everything in memory (tests)."""

    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False, timeout=10, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if path is not None:
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """An immediate (write-locked) transaction: atomic even across processes sharing the file."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _exec(self, sql: str, *params: Any) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            self._conn.commit()
            return rows

    # ── requests to people ──

    def add_request(self, **fields: Any) -> StoredRequest:
        with self.transaction() as conn:
            row = conn.execute("SELECT MAX(seq) FROM human_requests").fetchone()
            seq = (row[0] or FIRST_REQUEST_SEQ - 1) + 1
            request = StoredRequest(id=f"H-{seq}", status="pending", created_at=now(), **fields)
            conn.execute(
                """INSERT INTO human_requests (id, seq, run_id, kind, audience, question, action, facts, rules,
                     reasons, context, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request.id,
                    seq,
                    request.run_id,
                    request.kind,
                    request.audience,
                    request.question,
                    request.action,
                    json.dumps(request.facts),
                    json.dumps(request.rules),
                    json.dumps(request.reasons),
                    json.dumps(request.context),
                    request.status,
                    request.created_at,
                ),
            )
        return request

    def get_request(self, request_id: str) -> StoredRequest:
        rows = self._exec("SELECT * FROM human_requests WHERE id = ?", request_id)
        if not rows:
            raise KeyError(f"No request {request_id}")
        return self._request(rows[0])

    def list_requests(self, status: str | None = None, run_id: str | None = None) -> list[StoredRequest]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return [
            self._request(r)
            for r in self._exec(f"SELECT * FROM human_requests {where} ORDER BY seq", *params)
        ]

    def resolve_request(
        self, request_id: str, status: str, response: str | None, responder: str | None
    ) -> StoredRequest:
        self._exec(
            "UPDATE human_requests SET status = ?, response = ?, responder = ?, decided_at = ? WHERE id = ?",
            status,
            response,
            responder,
            now(),
            request_id,
        )
        return self.get_request(request_id)

    def amend_request(self, request_id: str, facts: dict[str, Any], context: dict[str, Any]) -> None:
        """Change the facts a request covers, e.g. when a supervisor approves a lower amount."""
        self._exec(
            "UPDATE human_requests SET facts = ?, context = ? WHERE id = ?",
            json.dumps(facts),
            json.dumps(context),
            request_id,
        )

    @staticmethod
    def _request(row: sqlite3.Row) -> StoredRequest:
        data = dict(row)
        data.pop("seq")
        for key in ("facts", "rules", "reasons", "context"):
            data[key] = json.loads(data[key])
        return StoredRequest(**data)

    # ── learned facts ──

    def add_fact(self, text: str, source: str) -> LearnedFact:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO learned_facts (text, source, created_at) VALUES (?, ?, ?)",
                (text.strip(), source, now()),
            )
            self._conn.commit()
            fact_id = cursor.lastrowid
        assert fact_id is not None
        return LearnedFact(fact_id, text.strip(), source, now())

    def facts(self) -> list[LearnedFact]:
        rows = self._exec(
            "SELECT id, text, source, created_at FROM learned_facts WHERE active = 1 ORDER BY id"
        )
        return [LearnedFact(**dict(r)) for r in rows]

    def forget_fact(self, fact_id: int) -> None:
        self._exec("UPDATE learned_facts SET active = 0 WHERE id = ?", fact_id)

    # ── episodes ──

    def add_episode(self, episode: Episode) -> None:
        self._exec(
            "INSERT OR REPLACE INTO episodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            episode.run_id,
            episode.category,
            episode.request,
            episode.outcome,
            episode.summary,
            json.dumps(episode.changes),
            int(episode.verified),
            episode.created_at,
        )

    def episodes(self, category: str | None = None, limit: int = 3) -> list[Episode]:
        if category:
            rows = self._exec(
                "SELECT * FROM episodes WHERE category = ? ORDER BY created_at DESC LIMIT ?", category, limit
            )
        else:
            rows = self._exec("SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?", limit)
        return [
            Episode(**{**dict(r), "changes": json.loads(r["changes"]), "verified": bool(r["verified"])})
            for r in rows
        ]
