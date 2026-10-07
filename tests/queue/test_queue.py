"""The task queue: ordering, de-duplication, safe claiming, waiting, crash recovery."""

import threading
from datetime import timedelta
from pathlib import Path

import pytest

from company_operator.memory.store import OperatorDB
from company_operator.queue import PRIORITY, TaskQueue


@pytest.fixture
def queue(tmp_path: Path) -> TaskQueue:
    return TaskQueue(OperatorDB(tmp_path / "operator.db"))


def test_priority_then_arrival_order(queue: TaskQueue) -> None:
    queue.enqueue("low", priority=PRIORITY["low"])
    queue.enqueue("normal 1")
    queue.enqueue("urgent", priority=PRIORITY["urgent"])
    queue.enqueue("normal 2")
    order = [t.text for t in iter(lambda: queue.claim("w"), None)]
    assert order == ["urgent", "normal 1", "normal 2", "low"]


def test_a_ticket_is_not_queued_twice_while_active(queue: TaskQueue) -> None:
    first = queue.enqueue("Resolve TKT-1001", ticket_id="TKT-1001")
    assert queue.enqueue("Resolve it again", ticket_id="TKT-1001").id == first.id
    queue.finish(first.id, "completed")
    assert queue.enqueue("New complaint", ticket_id="TKT-1001").id != first.id


def test_no_task_is_claimed_twice(tmp_path: Path) -> None:
    path = tmp_path / "operator.db"
    TaskQueue(OperatorDB(path))  # create schema
    for i in range(30):
        TaskQueue(OperatorDB(path)).enqueue(f"task {i}")
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        queue = TaskQueue(OperatorDB(path))  # each worker has its own connection, like separate processes
        while (task := queue.claim(name)) is not None:
            with lock:
                claimed.append(task.id)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(f"T-{i}" for i in range(1, 31))


def test_waiting_task_comes_back_when_due(queue: TaskQueue) -> None:
    task = queue.enqueue("needs approval")
    queue.claim("w")
    queue.wait(task.id, timedelta(hours=1))
    assert queue.claim("w") is None
    queue.wait(task.id, timedelta(seconds=-1))
    again = queue.claim("w")
    assert again is not None and again.id == task.id and again.attempts == 2


def test_task_of_a_dead_worker_is_reclaimed(tmp_path: Path) -> None:
    db = OperatorDB(tmp_path / "operator.db")
    crashed = TaskQueue(db, lease=timedelta(seconds=-1))  # its lease is already over
    task = crashed.enqueue("work")
    assert crashed.claim("dead-worker") is not None
    survivor = TaskQueue(db).claim("worker-2")
    assert survivor is not None and survivor.id == task.id and survivor.worker == "worker-2"


def test_live_lease_is_not_stolen(queue: TaskQueue) -> None:
    queue.enqueue("work")
    queue.claim("w1")
    assert queue.claim("w2") is None


def test_cancel_and_counts(queue: TaskQueue) -> None:
    a = queue.enqueue("a")
    queue.enqueue("b")
    assert queue.cancel(a.id).status == "cancelled"
    assert queue.counts() == {"cancelled": 1, "queued": 1}


def test_new_work_goes_before_rechecking_paused_tasks(queue: TaskQueue) -> None:
    """Regression: a parent waiting on sub-tasks was always due for a check and starved those sub-tasks."""
    parent = queue.enqueue("parent")
    queue.claim("w")
    queue.wait(parent.id, timedelta(seconds=-1))  # due for a check
    child = queue.enqueue("child", parent_id=parent.id)
    first = queue.claim("w")
    assert first is not None and first.id == child.id
