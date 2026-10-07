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
from urllib.parse import urlsplit

from company_operator.audit.log import Event
from company_operator.company_pack import load_pack
from company_operator.config import Settings
from company_operator.llm import build_llm
from company_operator.planning.llm_brain import LLMBrain, notify_current_run
from company_operator.runtime import Operator, RunStore, TaskRequest
from company_operator.runtime.environment import tool_environment
from company_operator.tools import default_registry
from company_operator.verify import IndependentVerifier, write_report


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
        "human.request": lambda: f"🙋 waiting for a human ({d['id']}): {d['question'][:160]}",
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


async def run(args: argparse.Namespace, settings: Settings) -> int:
    pack = load_pack(settings.company_pack_dir)
    llm = build_llm(settings, notify=notify_current_run)
    store = RunStore(settings.runs_dir)
    operator = Operator(LLMBrain(llm), IndependentVerifier(llm), default_registry(), pack, store)
    text = args.text or f"Resolve support ticket {args.ticket}."
    request = TaskRequest(
        text=text, source="ticket" if args.ticket else "api", ticket_id=args.ticket, requested_by="cli"
    )
    state = store.create(request)
    run_dir = store.run_dir(state.run_id)
    print(f"Run {state.run_id}: {text}", flush=True)
    async with tool_environment(pack, settings, run_dir) as tools:
        tools.log.subscribe(progress)
        await operator.run(state, tools)
    report = write_report(state, run_dir)
    print(f"\n{report['status_label']}" + (f": {state.outcome_reason}" if state.outcome_reason else ""))
    if state.summary:
        print(f"\n{state.summary}")
    print(f"\nReport: {run_dir / 'report.md'}")
    return 0 if state.phase == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="operator-run", description="Give the AI operator one task.")
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--ticket", help="Support ticket id to resolve, e.g. TKT-1001")
    task.add_argument("--text", help="A free-text request, e.g. a supervisor instruction")
    parser.add_argument("--sandbox", action="store_true", help="Start a fresh QuickBite sandbox for this run")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.headed:
        settings.browser_headless = False
    if not settings.llm_api_key and settings.llm_provider != "openai_compatible":
        print("Set ACO_LLM_API_KEY in .env first (see .env.example).", file=sys.stderr)
        return 2
    sandbox = embedded_sandbox(settings.sandbox_url) if args.sandbox else contextlib.nullcontext()
    with sandbox:
        return asyncio.run(run(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
