"""operator-worker: run background workers that take tasks from the queue.

    uv run operator-worker --workers 2            # against the sandbox at ACO_SANDBOX_URL
    uv run operator-worker --sandbox              # with a fresh embedded sandbox

Add work with `operator-run --enqueue --ticket TKT-1001`, the API (POST /tasks),
or the dashboard.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from datetime import timedelta

from company_operator.audit.log import Event
from company_operator.cli import embedded_sandbox, progress_line
from company_operator.company_pack import load_pack
from company_operator.config import Settings
from company_operator.llm import build_llm
from company_operator.memory.store import OperatorDB
from company_operator.planning.llm_brain import LLMBrain, notify_current_run
from company_operator.queue.tasks import Task, TaskQueue
from company_operator.queue.worker import Worker
from company_operator.runtime import Operator, RunStore
from company_operator.tools import default_registry
from company_operator.verify import IndependentVerifier


def _tag(task: Task) -> str:
    return f"[{task.id}{' ' + task.ticket_id if task.ticket_id else ''}]"


def show(task: Task, event: Event) -> None:
    if text := progress_line(event):
        print(f"{_tag(task)} {text.strip()}", flush=True)


def outcome_line(task: Task) -> str:
    """How a task ended, so a failure is visible here and not only in the API."""
    if task.status == "waiting":
        return f"{_tag(task)} Paused: waiting for a person (see the approval inbox)"
    reason = f": {task.error}" if task.error else ""
    return f"{_tag(task)} {task.status.capitalize()}{reason}"


def show_outcome(task: Task) -> None:
    print(outcome_line(task), file=sys.stderr if task.status == "failed" else sys.stdout, flush=True)


async def serve(settings: Settings, workers: int, check_interval: float) -> None:
    pack = load_pack(settings.company_pack_dir)
    db = OperatorDB(settings.db_path)
    llm = build_llm(settings, notify=notify_current_run)  # shared, so all workers respect one rate limit
    operator = Operator(
        LLMBrain(llm), IndependentVerifier(llm), default_registry(), pack, RunStore(settings.runs_dir), db=db
    )
    queue = TaskQueue(db)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    pool = [
        Worker(
            operator,
            queue,
            settings,
            name=f"worker-{i + 1}",
            check_interval=timedelta(seconds=check_interval),
            on_event=show,
            on_finish=show_outcome,
        )
        for i in range(workers)
    ]
    running = [asyncio.create_task(w.run_forever(stop)) for w in pool]

    def on_stop_signal() -> None:
        # First Ctrl+C: finish the task in hand, take no more. Second: stop now (the task goes back to the queue).
        if stop.is_set():
            for job in running:
                job.cancel()
            return
        stop.set()
        if any(w.busy for w in pool):
            print("Stopping after the current task. Press Ctrl+C again to stop now.", flush=True)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, on_stop_signal)
        except NotImplementedError:  # Windows: no loop signal handlers, so hand the signal to the loop
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(on_stop_signal))
    print(
        f"{workers} worker(s) waiting for tasks (Ctrl+C to stop). Queue: {queue.counts() or 'empty'}",
        flush=True,
    )
    for result in await asyncio.gather(*running, return_exceptions=True):
        if isinstance(result, Exception):  # a cancelled worker is a stop; anything else is a real error
            raise result
    print("Workers stopped.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operator-worker", description="Work through the operator's task queue."
    )
    parser.add_argument("--workers", type=int, default=1, help="Tasks worked on at the same time")
    parser.add_argument(
        "--check-interval", type=float, default=15, help="Seconds between checks on paused tasks"
    )
    parser.add_argument("--sandbox", action="store_true", help="Start a fresh QuickBite sandbox")
    parser.add_argument("--headed", action="store_true", help="Show browser windows")
    args = parser.parse_args(argv)
    settings = Settings()
    if args.headed:
        settings.browser_headless = False
    if not settings.llm_api_key and settings.llm_provider != "openai_compatible":
        print("Set ACO_LLM_API_KEY in .env first (see .env.example).", file=sys.stderr)
        return 2
    sandbox = embedded_sandbox(settings.sandbox_url) if args.sandbox else contextlib.nullcontext()
    with sandbox:
        asyncio.run(serve(settings, args.workers, args.check_interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
