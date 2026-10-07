"""Database access and small shared helpers for the QuickBite sandbox."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
IST = ZoneInfo("Asia/Kolkata")


class Database:
    """A thin wrapper around one SQLite file. Each call opens a short-lived connection."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def create(self) -> None:
        """Drop everything and recreate an empty schema."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.path}{suffix}").unlink(missing_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA_PATH.read_text())

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def one(self, sql: str, *params: Any) -> sqlite3.Row | None:
        with self.connect() as conn:
            row: sqlite3.Row | None = conn.execute(sql, params).fetchone()
            return row

    def all(self, sql: str, *params: Any) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def execute(self, sql: str, *params: Any) -> None:
        with self.connect() as conn:
            conn.execute(sql, params)


def next_id(conn: sqlite3.Connection, table: str, prefix: str, start: int) -> str:
    """Next sequential business id such as RF-70004, continuing from the highest existing one."""
    row = conn.execute(
        f"SELECT MAX(CAST(SUBSTR(id, {len(prefix) + 1}) AS INTEGER)) FROM {table} WHERE id LIKE ?",
        (f"{prefix}%",),
    ).fetchone()
    current = row[0] if row and row[0] is not None else start - 1
    return f"{prefix}{current + 1}"


# ───────────────────────── time ─────────────────────────


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def now_iso() -> str:
    return iso(utcnow())


def ago(**kwargs: float) -> str:
    return iso(utcnow() - timedelta(**kwargs))


def fmt_time(value: str | None) -> str:
    """Render a stored UTC timestamp the way an Indian back-office tool would: IST, to the minute."""
    if not value:
        return "—"
    return parse(value).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def minutes_between(start: str, end: str) -> int:
    return round((parse(end) - parse(start)).total_seconds() / 60)


# ───────────────────────── money ─────────────────────────


def rupees(paise: int | None) -> str:
    """Format paise as an Indian rupee amount, e.g. 64500 -> ₹645.00."""
    if paise is None:
        return "—"
    return f"₹{paise / 100:,.2f}"


def parse_rupees(text: str) -> int:
    """Parse a rupee amount typed into a form ("645", "645.50", "₹1,200") into paise."""
    cleaned = text.replace("₹", "").replace(",", "").strip()
    value = round(float(cleaned) * 100)
    if value <= 0:
        raise ValueError("Amount must be greater than zero.")
    return value
