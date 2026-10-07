"""Command-line runner: give the operator one task and watch it work.

    uv run operator-run --ticket TKT-1001 --sandbox
    uv run operator-run --text "Which late-delivery tickets are open today?" --sandbox

--sandbox starts a fresh QuickBite sandbox in-process; without it, the
operator uses the one at ACO_SANDBOX_URL (start it with `make sandbox`).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

from company_operator.audit.log import Event, EventLog
from company_operator.company_pack import load_pack
from company_operator.config import Settings
from company_operator.llm import build_llm
from company_operator.memory.store import OperatorDB
from company_operator.planning.llm_brain import LLMBrain, notify_current_run
from company_operator.policy import PolicyEngine
from company_operator.runtime import Operator, RunState, RunStore, TaskRequest
from company_operator.runtime.environment import tool_environment
from company_operator.tools import default_registry
from company_operator.tools.human import HumanChannel
from company_operator.verify import IndependentVerifier, write_report

WATCH_INTERVAL = 10.0


def progress(event: Event) -> None:
    """One line per interesting event."""
    d = event.data
    line = {
        "phase": lambda: f"── {d['from']} → {d['to']}",
        "contract.created": lambda: f"📝 contract: {d['contract']['outcome']}",
        "plan.created": lambda: f"🗺  plan v{d['version']}: " + " → ".join(s["id"] for s in d["steps"]),
        "tool.call": lambda: f"   ▶ {d['tool']} {_short(d['arguments'])}",
        "tool.result": lambda: "" if d["ok"] else f"   ✖ {d['error']}: {_note(d['output'])}",
        "step.done": lambda: f"   ✔ {d['step']}: {d['outcome'][:140]}",
        "adapt.decision": lambda: f"   ↻ adapt [{d['rule']}]",
        "verify.result": lambda: (
            "🔍 verification: "
            + ", ".join(f"{r['criterion_id']} {'✅' if r['passed'] else '❌'}" for r in d["results"])
        ),
        "human.request": lambda: (
            f"🙋 waiting for {d['audience']} ({d['id']}, {d['kind']}): {d['question'][:160]}"
        ),
        "human.answered": lambda: f"💬 answer on {d['id']}: {d['response'][:160]}",
        "run.handover": lambda: f"🤝 handing over: {d['reason']}",
        "llm.cooldown": lambda: f"   ⏳ {d['model']} rate-limited, cooling down {d['seconds']:.0f}s",
        "run.escalated": lambda: f"⚠️  escalated: {d['reason']}",
    }.get(event.type)
    if line and (text := line()):
        print(text, flush=True)


def _short(arguments: dict[str, object]) -> str:
    shown = {k: v for k, v in arguments.items() if k != "ref"}
    return str(shown)[:150] if shown else ""


def _note(output: str) -> str:
    return next(
        (ln[6:] for ln in output.splitlines() if ln.startswith("Note: ")),
        output.splitlines()[0] if output else "",
    )[:160]


@contextlib.contextmanager
def embedded_sandbox(url: str) -> Iterator[None]:
    import uvicorn

    from sandbox.quickbite.app import create_app

    parts = urlsplit(url)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(), host=parts.hostname or "127.0.0.1", port=parts.port or 8100, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise SystemExit(f"Could not start the sandbox on {url} (is something else using the port?)")
        time.sleep(0.05)
    print(f"QuickBite sandbox running at {url}", flush=True)
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def finish(state: RunState, run_dir: Path) -> int:
    report = write_report(state, run_dir)
    print(f"\n{report['status_label']}" + (f": {state.outcome_reason}" if state.outcome_reason else ""))
    if state.phase == "awaiting_human" and state.pending_human:
        print(
            f"Waiting on {state.pending_human}. Decide with --approve/--reject, then: operator-run --resume {state.run_id}"
        )
    if state.summary:
        print(f"\n{state.summary}")
    print(f"\nReport: {run_dir / 'report.md'}")
    return 0 if state.phase in ("completed", "awaiting_human") else 1


async def run(args: argparse.Namespace, settings: Settings) -> int:
    pack = load_pack(settings.company_pack_dir)
    llm = build_llm(settings, notify=notify_current_run)
    store = RunStore(settings.runs_dir)
    db = OperatorDB(settings.db_path)
    operator = Operator(LLMBrain(llm), IndependentVerifier(llm), default_registry(), pack, store, db=db)

    if args.resume:
        state = store.load(args.resume)
        print(f"Resuming run {state.run_id} ({state.phase})", flush=True)
    else:
        text = args.text or f"Resolve support ticket {args.ticket}."
        request = (
            TaskRequest(text=text, source="ticket", ticket_id=args.ticket, requested_by="cli")
            if args.ticket
            else TaskRequest(text=text, source="api", requested_by="cli")
        )
        state = store.create(request)
        print(f"Run {state.run_id}: {text}", flush=True)
    run_dir = store.run_dir(state.run_id)

    async with tool_environment(pack, settings, run_dir, db) as tools:
        tools.log.subscribe(progress)
        if not args.resume:
            await operator.run(state, tools)
        deadline = time.monotonic() + args.watch
        while True:
            if args.resume or state.phase == "awaiting_human":
                await operator.resume(state, tools)
            if state.phase != "awaiting_human" or time.monotonic() >= deadline:
                break
            await asyncio.sleep(WATCH_INTERVAL)  # waiting for a person or a customer: look again shortly
    return finish(state, run_dir)


def inbox(settings: Settings) -> int:
    pending = OperatorDB(settings.db_path).list_requests(status="pending")
    if not pending:
        print("Nothing is waiting for a person.")
    for r in pending:
        print(f"{r.id} [{r.kind}] run {r.run_id}: {r.question}")
        if r.action:
            print(f"    wants: {r.action} {r.facts}")
            print(f"    because: {'; '.join(r.reasons)}")
    return 0


def decide(settings: Settings, request_id: str, approved: bool, note: str, remember: bool) -> int:
    log = EventLog()
    pack = load_pack(settings.company_pack_dir)
    channel = HumanChannel(PolicyEngine(pack, log), log, OperatorDB(settings.db_path))
    request = channel.decide(request_id, approved, approver=settings.supervisor, note=note, remember=remember)
    print(
        f"{request.id} {request.status} by {request.responder}"
        + (" (remembered as a company fact)" if remember and note else "")
    )
    if request.run_id:
        print(f"Continue the run with: operator-run --resume {request.run_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operator-run", description="Give the AI operator a task, or act on its requests."
    )
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--ticket", help="Support ticket id to resolve, e.g. TKT-1001")
    task.add_argument("--text", help="A free-text request, e.g. a supervisor instruction")
    task.add_argument("--resume", metavar="RUN_ID", help="Continue a paused run")
    task.add_argument("--inbox", action="store_true", help="List requests waiting for a person")
    task.add_argument("--approve", metavar="REQUEST_ID", help="Approve a request, e.g. H-1001")
    task.add_argument("--reject", metavar="REQUEST_ID", help="Reject a request, e.g. H-1001")
    parser.add_argument("--note", default="", help="Reason for an approval or rejection")
    parser.add_argument(
        "--remember", action="store_true", help="Keep the note as a company fact for future runs"
    )
    parser.add_argument(
        "--watch", type=float, default=0, metavar="SECONDS", help="Keep checking a paused run this long"
    )
    parser.add_argument("--sandbox", action="store_true", help="Start a fresh QuickBite sandbox for this run")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.inbox:
        return inbox(settings)
    if args.approve or args.reject:
        return decide(settings, args.approve or args.reject, bool(args.approve), args.note, args.remember)
    if args.headed:
        settings.browser_headless = False
    if not settings.llm_api_key and settings.llm_provider != "openai_compatible":
        print("Set ACO_LLM_API_KEY in .env first (see .env.example).", file=sys.stderr)
        return 2
    if args.sandbox and args.resume:
        print(
            "--sandbox starts a fresh world; resume against the sandbox the run used (make sandbox).",
            file=sys.stderr,
        )
        return 2
    sandbox = embedded_sandbox(settings.sandbox_url) if args.sandbox else contextlib.nullcontext()
    with sandbox:
        return asyncio.run(run(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
