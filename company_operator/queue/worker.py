"""Background workers: take tasks from the queue and see them through.

A worker claims the next task and starts its run, or continues it from the
checkpoint. It keeps the task's lease alive while working and files the
outcome. A run that pauses for a person goes back to the queue as `waiting`
and is looked at again periodically, so approvals, answers, customer replies
and finished sub-tasks are picked up without anyone typing `--resume`.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import Callable
from datetime import timedelta

from company_operator.audit.log import Event
from company_operator.config import Settings
from company_operator.queue.tasks import Task, TaskQueue
from company_operator.runtime import Operator, TaskRequest
from company_operator.runtime.environment import tool_environment
from company_operator.verify import write_report

FINAL = {"completed": "completed", "escalated": "escalated", "failed": "failed"}


class Worker:
    def __init__(
        self,
        operator: Operator,
        queue: TaskQueue,
        settings: Settings,
        *,
        name: str | None = None,
        check_interval: timedelta = timedelta(seconds=15),
        poll_interval: float = 2.0,
        on_event: Callable[[Task, Event], None] | None = None,
        on_finish: Callable[[Task], None] | None = None,
    ) -> None:
        self.operator = operator
        self.queue = queue
        self.settings = settings
        self.name = name or f"{socket.gethostname()}-{id(self) % 10_000}"
        self.check_interval = check_interval
        self.poll_interval = poll_interval
        self.on_event = on_event
        self.on_finish = on_finish
        self.busy = False

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if await self.run_once() is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)

    async def run_once(self) -> Task | None:
        """Claim and work on one task. Returns it, or None if nothing was due."""
        task = self.queue.claim(self.name)
        if task is None:
            return None
        heartbeat = asyncio.create_task(self._heartbeat(task.id))
        self.busy = True
        try:
            await self._work(task)
        except Exception as exc:  # the task fails, the worker lives on
            self.queue.finish(task.id, "failed", error=f"{type(exc).__name__}: {exc}")
        except asyncio.CancelledError:  # stopped mid-task: the run is checkpointed, so hand the task back
            self.queue.release(task.id, self.name)
            raise
        finally:
            self.busy = False
            heartbeat.cancel()
        outcome = self.queue.get(task.id)
        if self.on_finish is not None:
            self.on_finish(outcome)
        return outcome

    async def _work(self, task: Task) -> None:
        store = self.operator.store
        if task.run_id:
            state = store.load(task.run_id)
        else:
            state = store.create(
                TaskRequest(
                    text=task.text,
                    source=task.source if task.source in ("ticket", "supervisor", "api") else "api",  # type: ignore[arg-type]
                    ticket_id=task.ticket_id,
                    requested_by=task.requested_by,
                )
            )
            self.queue.attach_run(task.id, state.run_id)
        run_dir = store.run_dir(state.run_id)

        if not state.finished:
            async with tool_environment(
                self.operator.pack, self.settings, run_dir, self.operator.db
            ) as tools:
                tools.queue, tools.task_id = self.queue, task.id
                if self.on_event is not None:
                    on_event = self.on_event
                    tools.log.subscribe(lambda event: on_event(task, event))
                if state.phase == "awaiting_human":
                    await self.operator.resume(state, tools)
                else:
                    await self.operator.run(state, tools)
        write_report(state, run_dir)

        if state.phase == "awaiting_human":
            self.queue.wait(task.id, self.check_interval)
        else:
            self.queue.finish(task.id, FINAL.get(state.phase, "failed"), error=state.outcome_reason or None)

    async def _heartbeat(self, task_id: str) -> None:
        interval = max(1.0, self.queue.lease.total_seconds() / 3)
        while True:
            await asyncio.sleep(interval)
            self.queue.renew(task_id, self.name)
