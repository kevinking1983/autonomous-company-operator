"""Run an eval from the command line.

    uv run operator-eval --suite smoke --sandbox          # 3 quick runs on a fresh in-process sandbox
    uv run operator-eval --suite core                     # all 14 scenarios against the running sandbox
    uv run operator-eval --cases missing_item --profiles flaky,storm
    uv run operator-eval --resume 20261007-180000-ab12    # carry on after an interruption or a quota stop
    uv run operator-eval --list                           # suites, cases and fault profiles

Each run resets the sandbox, so do not point it at a sandbox someone is using.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

from company_operator.cli import embedded_sandbox
from company_operator.company_pack import load_pack
from company_operator.config import Settings
from company_operator.evals.cases import CASES
from company_operator.evals.harness import EvalHarness, EvalStore, resolve_pairs, scorecard
from company_operator.evals.profiles import PROFILES, SUITES
from company_operator.evals.world import Sandbox
from company_operator.llm import build_llm
from company_operator.memory.store import OperatorDB
from company_operator.planning.llm_brain import LLMBrain, notify_current_run
from company_operator.runtime import Operator, RunStore
from company_operator.tools import default_registry
from company_operator.verify import IndependentVerifier

MODEL_CALLS_PER_RUN = 30  # a typical run, for the cost estimate


def print_catalog() -> None:
    print("Suites:")
    for name, suite in SUITES.items():
        print(f"  {name:12} {suite['label']}: {suite['description']}")
    print("\nCases:")
    for case in CASES.values():
        print(f"  {case.key:28} {case.ticket_id}  {case.summary}")
    print("\nFault profiles:")
    for p in PROFILES.values():
        print(f"  {p.name:15} {p.description}")


async def run(args: argparse.Namespace, settings: Settings) -> int:
    store = EvalStore(settings.evals_dir)
    if args.resume:
        eval_id = args.resume
    else:
        pairs = resolve_pairs(args.suite, _split(args.cases), _split(args.profiles))
        label = SUITES[args.suite]["label"] if args.suite else f"{len(pairs)} runs"
        eval_id = args.id or store.create(pairs, label=label, models=settings.model_list)
    meta = store.meta(eval_id)
    print(
        f"Eval {eval_id}: {len(meta['pairs'])} runs, about {len(meta['pairs']) * MODEL_CALLS_PER_RUN} model calls.",
        flush=True,
    )

    pack = load_pack(settings.company_pack_dir)
    llm = build_llm(settings, notify=notify_current_run)

    def make_operator(runs: RunStore, db: OperatorDB) -> Operator:
        return Operator(LLMBrain(llm), IndependentVerifier(llm), default_registry(), pack, runs, db=db)

    harness = EvalHarness(
        settings,
        pack,
        store,
        Sandbox(settings.sandbox_url, settings.sandbox_control_key),
        make_operator,
        timeout_seconds=args.timeout_minutes * 60,
        progress=lambda line: print(line, flush=True),
    )
    try:
        meta = await harness.run(eval_id)
    except BaseException:
        store.update(eval_id, status="stopped", current=None)
        raise
    card = scorecard(store.results(eval_id))
    print(
        f"\n{meta['status']}: {card['passed']}/{card['scored']} passed"
        f" ({card['errors']} errors, {card['unsafe_runs']} unsafe). "
        f"Scorecard: {store.dir(eval_id) / 'scorecard.md'}"
    )
    return 0 if meta["status"] == "completed" else 1


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the operator on known scenarios under injected faults.")
    parser.add_argument("--suite", choices=list(SUITES), help="A named set of runs")
    parser.add_argument("--cases", help="Comma-separated case keys (default: all)")
    parser.add_argument("--profiles", help="Comma-separated fault profiles (default: clean)")
    parser.add_argument("--resume", metavar="EVAL_ID", help="Continue an earlier eval")
    parser.add_argument("--id", help=argparse.SUPPRESS)  # an eval created by the API
    parser.add_argument("--sandbox", action="store_true", help="Start a fresh sandbox in-process")
    parser.add_argument(
        "--timeout-minutes", type=float, default=15, help="Give up on one run after this long"
    )
    parser.add_argument("--list", action="store_true", help="List suites, cases and fault profiles")
    args = parser.parse_args()
    if args.list:
        print_catalog()
        return
    settings = Settings()
    with embedded_sandbox(settings.sandbox_url) if args.sandbox else contextlib.nullcontext():
        sys.exit(asyncio.run(run(args, settings)))


if __name__ == "__main__":
    main()
