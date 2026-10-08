"""Operator API: health, the approval inbox, questions, and the operator's memory.

The dashboard (step 10) is built on these endpoints. Runs themselves are
started and resumed by the worker (step 9) or the operator-run CLI.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from company_operator import __version__
from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack, load_pack
from company_operator.config import get_settings
from company_operator.evals import CASES, PROFILES, SUITES
from company_operator.evals.harness import EvalStore, resolve_pairs, scorecard
from company_operator.evals.world import Sandbox
from company_operator.memory.store import OperatorDB
from company_operator.policy import PolicyEngine
from company_operator.queue import PRIORITY, TaskQueue
from company_operator.runtime import RunStore
from company_operator.tools.base import LIVE_FRAME
from company_operator.tools.human import HumanChannel
from company_operator.verify.report import build_report, load_events

app = FastAPI(title="Autonomous Company Operator", version=__version__)
STREAM_POLL_SECONDS = 0.5
STREAM_IDLE_SECONDS = 3.0


@lru_cache
def get_db() -> OperatorDB:
    return OperatorDB(get_settings().db_path)


@lru_cache
def get_pack() -> CompanyPack:
    return load_pack(get_settings().company_pack_dir)


Db = Annotated[OperatorDB, Depends(get_db)]
Pack = Annotated[CompanyPack, Depends(get_pack)]


def channel(db: OperatorDB, pack: CompanyPack) -> HumanChannel:
    # Decisions are recorded here; the run that is waiting rebuilds its grant from the stored request.
    log = EventLog()
    return HumanChannel(PolicyEngine(pack, log), log, db)


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "version": __version__, "company_pack": settings.company_pack}


# ───────────────────────── approval inbox and questions ─────────────────────────


@app.get("/requests")
def list_requests(db: Db, status: str | None = "pending", run_id: str | None = None) -> list[dict[str, Any]]:
    """Requests to people. Default: everything still waiting for an answer."""
    return [vars(r) for r in db.list_requests(status=status or None, run_id=run_id)]


@app.get("/requests/{request_id}")
def get_request(request_id: str, db: Db) -> dict[str, Any]:
    try:
        return vars(db.get_request(request_id))
    except KeyError:
        raise HTTPException(404, f"No request {request_id}") from None


class DecisionBody(BaseModel):
    approved: bool
    approver: str = Field(min_length=1)
    note: str = ""
    remember: bool = Field(
        False, description="Teach the operator: keep the note as a company fact for future runs."
    )
    amount: float | None = Field(
        None, gt=0, description="Approve a lower amount than requested (never a higher one)."
    )


@app.post("/requests/{request_id}/decision")
def decide(
    request_id: str,
    body: DecisionBody,
    db: Db,
    pack: Pack,
) -> dict[str, Any]:
    try:
        request = channel(db, pack).decide(
            request_id, body.approved, body.approver, body.note, body.remember, body.amount
        )
    except KeyError:
        raise HTTPException(404, f"No request {request_id}") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return vars(request)


class AnswerBody(BaseModel):
    answer: str = Field(min_length=1)
    responder: str = Field(min_length=1)


@app.post("/requests/{request_id}/answer")
def answer(request_id: str, body: AnswerBody, db: Db, pack: Pack) -> dict[str, Any]:
    try:
        request = db.get_request(request_id)
    except KeyError:
        raise HTTPException(404, f"No request {request_id}") from None
    if request.kind != "clarification" or request.status != "pending":
        raise HTTPException(409, f"{request_id} is not an open question")
    return vars(channel(db, pack).answer(request_id, body.answer, body.responder))


# ───────────────────────── memory ─────────────────────────


@app.get("/memory/facts")
def facts(db: Db, pack: Pack) -> list[dict[str, Any]]:
    """Everything the operator treats as company fact: the pack's own, then what people taught it."""
    builtin = [{"id": f.id, "text": f.text, "source": f.source, "learned": False} for f in pack.facts]
    learned = [
        {
            "id": f"learned-{f.id}",
            "text": f.text,
            "source": f.source,
            "learned": True,
            "created_at": f.created_at,
        }
        for f in db.facts()
    ]
    return builtin + learned


class FactBody(BaseModel):
    text: str = Field(min_length=5)
    source: str = "added by a supervisor"


@app.post("/memory/facts")
def add_fact(body: FactBody, db: Db) -> dict[str, Any]:
    return vars(db.add_fact(body.text, body.source))


@app.delete("/memory/facts/{fact_id}")
def forget_fact(fact_id: int, db: Db) -> dict[str, str]:
    db.forget_fact(fact_id)
    return {"status": "forgotten"}


@app.get("/memory/episodes")
def episodes(db: Db, category: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    return [vars(e) for e in db.episodes(category, limit=limit)]


# ───────────────────────── tasks and runs ─────────────────────────


@lru_cache
def get_store() -> RunStore:
    return RunStore(get_settings().runs_dir)


Store = Annotated[RunStore, Depends(get_store)]


@lru_cache
def get_evals() -> EvalStore:
    return EvalStore(get_settings().evals_dir)


Evals = Annotated[EvalStore, Depends(get_evals)]


def get_scoped_store(store: Store, evals: Evals, scope: str | None = None) -> RunStore:
    """The operator's own runs, or with ?scope=eval:<eval id>:<case>:<profile> a run made by an eval."""
    if not scope:
        return store
    match = re.fullmatch(r"eval:([\w-]+):(\w+):(\w+)", scope)
    if not match or match.group(1) not in evals.list():
        raise HTTPException(404, f"No such scope {scope}")
    eval_id, case, profile = match.groups()
    return RunStore(evals.dir(eval_id) / "runs" / f"{case}__{profile}" / "runs")


RunScope = Annotated[RunStore, Depends(get_scoped_store)]


def queue_of(db: OperatorDB) -> TaskQueue:
    return TaskQueue(db)


def run_brief(store: RunStore, run_id: str | None) -> dict[str, Any] | None:
    if not run_id or not (store.run_dir(run_id) / "state.json").exists():
        return None
    state = store.load(run_id)
    return {
        "run_id": run_id,
        "phase": state.phase,
        "current_step": state.current_step,
        "outcome": state.contract.outcome if state.contract else None,
        "summary": state.summary,
        "reason": state.outcome_reason,
        "pending_human": state.pending_human,
        "verified": state.verification.passed if state.verification else None,
        "category": state.contract.category if state.contract else None,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


class TaskBody(BaseModel):
    text: str | None = None
    ticket_id: str | None = None
    priority: str = Field("normal", pattern="^(low|normal|high|urgent)$")
    requested_by: str = "dashboard"


@app.post("/tasks")
def create_task(body: TaskBody, db: Db) -> dict[str, Any]:
    if not body.text and not body.ticket_id:
        raise HTTPException(422, "Give a request text or a ticket id")
    text = body.text or f"Resolve support ticket {body.ticket_id}."
    source = "ticket" if body.ticket_id and not body.text else "supervisor"
    task = queue_of(db).enqueue(
        text,
        ticket_id=body.ticket_id,
        source=source,
        requested_by=body.requested_by,
        priority=PRIORITY[body.priority],
    )
    return vars(task)


class BulkBody(BaseModel):
    ticket_ids: list[str] = Field(min_length=1, max_length=50)
    priority: str = Field("normal", pattern="^(low|normal|high|urgent)$")
    requested_by: str = "dashboard"


@app.post("/tasks/bulk")
def create_tasks(body: BulkBody, db: Db) -> list[dict[str, Any]]:
    """Queue several tickets at once. A ticket already queued or running is not queued twice."""
    ids = list(dict.fromkeys(t.strip().upper() for t in body.ticket_ids if t.strip()))
    bad = [t for t in ids if not re.fullmatch(r"TKT-\d+", t)]
    if bad or not ids:
        raise HTTPException(422, f"Not ticket ids: {', '.join(bad) or 'none given'}")
    queue = queue_of(db)
    return [
        vars(
            queue.enqueue(
                f"Resolve support ticket {t}.",
                ticket_id=t,
                source="ticket",
                requested_by=body.requested_by,
                priority=PRIORITY[body.priority],
            )
        )
        for t in ids
    ]


@app.get("/tasks")
def list_tasks(
    db: Db, store: Store, status: str | None = None, parent_id: str | None = None
) -> list[dict[str, Any]]:
    return [
        vars(t) | {"run": run_brief(store, t.run_id)}
        for t in queue_of(db).list(status=status, parent_id=parent_id)
    ]


@app.get("/tasks/{task_id}")
def get_task(task_id: str, db: Db, store: Store) -> dict[str, Any]:
    queue = queue_of(db)
    try:
        task = queue.get(task_id)
    except KeyError:
        raise HTTPException(404, f"No task {task_id}") from None
    return vars(task) | {
        "run": run_brief(store, task.run_id),
        "children": [vars(c) | {"run": run_brief(store, c.run_id)} for c in queue.list(parent_id=task_id)],
    }


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, db: Db) -> dict[str, Any]:
    try:
        return vars(queue_of(db).cancel(task_id))
    except KeyError:
        raise HTTPException(404, f"No task {task_id}") from None


@app.get("/stats")
def stats(db: Db) -> dict[str, Any]:
    return {
        "tasks": queue_of(db).counts(),
        "waiting_for_people": len(db.list_requests(status="pending")),
        "learned_facts": len(db.facts()),
        "episodes": len(db.episodes(limit=10_000)),
    }


def _run_dir(store: RunStore, run_id: str) -> Path:
    if not re.fullmatch(r"[\w-]+", run_id) or not (store.run_dir(run_id) / "state.json").exists():
        raise HTTPException(404, f"No run {run_id}")
    return store.run_dir(run_id)


@app.get("/runs/{run_id}")
def get_run(run_id: str, store: RunScope) -> dict[str, Any]:
    _run_dir(store, run_id)
    return store.load(run_id).model_dump()


@app.get("/runs/{run_id}/report")
def get_report(run_id: str, store: RunScope) -> dict[str, Any]:
    run_dir = _run_dir(store, run_id)
    return build_report(store.load(run_id), load_events(run_dir), run_dir)


@app.get("/runs/{run_id}/events")
def get_events(run_id: str, store: RunScope, after: int = 0) -> list[dict[str, Any]]:
    return [e for e in load_events(_run_dir(store, run_id)) if e["seq"] > after]


@app.get("/runs/{run_id}/stream")
async def stream_events(run_id: str, store: RunScope, after: int = 0) -> StreamingResponse:
    """Server-Sent Events: every audit event of the run as it happens (the dashboard's live view)."""
    run_dir = _run_dir(store, run_id)

    async def events() -> AsyncIterator[str]:
        sent = after
        idle = 0.0
        while True:
            fresh = [e for e in load_events(run_dir) if e["seq"] > sent]
            for event in fresh:
                sent = event["seq"]
                yield f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
            idle = 0.0 if fresh else idle + STREAM_POLL_SECONDS
            if idle >= STREAM_IDLE_SECONDS and store.load(run_id).phase in (
                "completed",
                "escalated",
                "failed",
            ):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(STREAM_POLL_SECONDS)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/runs/{run_id}/evidence/{name}")
def evidence(run_id: str, name: str, store: RunScope) -> FileResponse:
    folder = _run_dir(store, run_id) / "evidence"
    path = (folder / name).resolve()
    if path.parent != folder.resolve() or not path.is_file():  # no ../ escapes
        raise HTTPException(404, f"No evidence {name}")
    return FileResponse(path)


# ───────────────────────── views for the dashboard ─────────────────────────


@app.get("/runs/{run_id}/live.jpg")
def live_frame(run_id: str, store: RunScope) -> FileResponse:
    """What the operator's browser shows right now (updated after every browser action)."""
    path = _run_dir(store, run_id) / LIVE_FRAME
    if not path.exists():
        raise HTTPException(404, "No browser frame yet")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/runs")
def list_runs(store: Store, limit: int = 50) -> list[dict[str, Any]]:
    """Every run, newest first, including ones started from the CLI rather than the queue."""
    briefs = [run_brief(store, run_id) for run_id in store.list()[:limit]]
    return [b | {"request": store.load(b["run_id"]).request.model_dump()} for b in briefs if b]


@app.get("/runs/{run_id}/pages")
def run_pages(run_id: str, store: RunScope) -> list[dict[str, Any]]:
    """The pages the operator looked at during the run, latest version of each: what it actually saw."""
    _run_dir(store, run_id)
    pages: dict[str, dict[str, Any]] = {}
    for obs in store.load(run_id).observations:
        if not obs.output.startswith("URL: "):
            continue
        lines = obs.output.splitlines()
        url = lines[0][5:].split("?")[0]
        title = next((ln[7:] for ln in lines if ln.startswith("Title: ")), url)
        pages.pop(url, None)
        pages[url] = {"seq": obs.seq, "url": url, "title": title, "text": obs.output}
    return list(reversed(pages.values()))


@app.get("/requests/{request_id}/context")
def request_context(request_id: str, db: Db, store: Store) -> dict[str, Any]:
    """Everything a supervisor needs to decide: the request, its run, and the evidence the operator saw."""
    try:
        request = db.get_request(request_id)
    except KeyError:
        raise HTTPException(404, f"No request {request_id}") from None
    run = run_brief(store, request.run_id)
    evidence = []
    if run:
        folder = store.run_dir(request.run_id or "") / "evidence"
        evidence = sorted(p.name for p in folder.iterdir()) if folder.exists() else []
    return {
        "request": vars(request),
        "run": run,
        "evidence": evidence,
        "pages": run_pages(request.run_id, store) if run and request.run_id else [],
    }


MONEY_ACTIONS = ("payments.refund", "payments.issue_coupon")


@app.get("/overview")
def overview(db: Db, store: Store) -> dict[str, Any]:
    """Headline numbers for the command centre."""
    episodes = db.episodes(limit=10_000)
    outcomes: dict[str, int] = {}
    money = {"payments.refund": 0.0, "payments.issue_coupon": 0.0}
    for e in episodes:
        outcomes[e.outcome] = outcomes.get(e.outcome, 0) + 1
        for change in e.changes:
            if change.get("action") in MONEY_ACTIONS:
                money[str(change["action"])] += float((change.get("facts") or {}).get("amount", 0) or 0)
    finished = len(episodes)
    verified = sum(1 for e in episodes if e.verified)
    requests = db.list_requests(status=None)
    approvals = [r for r in requests if r.kind == "approval" and r.status != "pending"]
    approved = sum(1 for r in approvals if r.status == "approved")
    with_people = {r.run_id for r in requests if r.kind in ("approval", "clarification")}
    auto = sum(1 for e in episodes if e.outcome == "completed" and e.run_id not in with_people)
    return {
        "tasks": queue_of(db).counts(),
        "runs_finished": finished,
        "outcomes": outcomes,
        "verified": verified,
        "verified_rate": round(verified / finished, 3) if finished else None,
        "refunded": round(money["payments.refund"], 2),
        "coupons": round(money["payments.issue_coupon"], 2),
        "waiting_for_people": len(db.list_requests(status="pending")),
        "auto_resolution_rate": round(auto / finished, 3) if finished else None,
        "approvals_decided": len(approvals),
        "approval_rate": round(approved / len(approvals), 3) if approvals else None,
        **run_times(store, db),
        "learned_facts": len(db.facts()),
    }


def _minutes(start: str, end: str) -> float:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60


def run_times(store: RunStore, db: OperatorDB) -> dict[str, float | None]:
    """Average time from start to finish of finished runs, and how much of it was spent waiting for people."""
    waits: dict[str, float] = {}
    for r in db.list_requests(status=None):
        if r.run_id and r.decided_at and r.kind in ("approval", "clarification"):
            waits[r.run_id] = waits.get(r.run_id, 0.0) + _minutes(r.created_at, r.decided_at)
    totals, waiting = [], []
    for run_id in store.list():
        state = store.load(run_id)
        if state.finished:
            totals.append(_minutes(state.created_at, state.updated_at))
            waiting.append(min(waits.get(run_id, 0.0), totals[-1]))
    if not totals:
        return {"avg_minutes": None, "avg_waiting_minutes": None}
    return {
        "avg_minutes": round(sum(totals) / len(totals), 1),
        "avg_waiting_minutes": round(sum(waiting) / len(waiting), 1),
    }


ACTIVITY_TYPES = {
    "contract.created", "contract.revised", "plan.created", "step.done", "verify.result", "run.completed",
    "run.escalated", "run.handover", "human.request", "human.decided", "human.answered", "guard.blocked",
    "adapt.decision", "subtasks.escalated",
}  # fmt: skip


@app.get("/activity")
def activity(store: Store, limit: int = 40) -> list[dict[str, Any]]:
    """The latest notable events across recent runs: the command centre's live feed."""
    items: list[dict[str, Any]] = []
    for run_id in store.list()[:15]:
        state = store.load(run_id)
        label = state.request.ticket_id or state.request.text[:60]
        for event in load_events(store.run_dir(run_id))[-200:]:
            if event["type"] in ACTIVITY_TYPES:
                items.append(event | {"run_id": run_id, "label": label})
    return sorted(items, key=lambda e: e["at"], reverse=True)[:limit]


@app.get("/company-pack")
def company_pack(pack: Pack) -> dict[str, Any]:
    """What the operator knows about the company: read-only, for people to inspect."""
    return {
        "company": pack.company.model_dump(),
        "systems": {
            k: v.model_dump(exclude={"credentials": {"sandbox_default"}}) for k, v in pack.systems.items()
        },
        "actions": [a.model_dump() for a in pack.actions.values()],
        "forbidden": [f.model_dump() for f in pack.forbidden],
        "compensation": pack.compensation.model_dump(),
        "approvals": pack.approvals.model_dump(),
        "sops": [s.model_dump(exclude={"path"}) for s in pack.sops.values()],
        "records": {k: v.model_dump() for k, v in pack.records.items()},
        "guides": {k: v.model_dump() for k, v in pack.guides.items()},
    }


# ───────────────────────── reliability lab: evals and the fault switchboard ─────────────────────────


@lru_cache
def get_sandbox() -> Sandbox:
    settings = get_settings()
    return Sandbox(settings.sandbox_url, settings.sandbox_control_key, timeout=10)


def launch_eval(eval_dir: Path, eval_id: str) -> int:
    """Run the eval in its own process (it takes minutes to hours); its output goes to log.txt."""
    with (eval_dir / "log.txt").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "company_operator.evals.cli", "--id", eval_id],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # keeps running if the API restarts
        )
    return process.pid


@lru_cache
def get_launcher() -> Callable[[Path, str], int]:
    return launch_eval


SandboxDep = Annotated[Sandbox, Depends(get_sandbox)]
Launcher = Annotated[Callable[[Path, str], int], Depends(get_launcher)]


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _eval_meta(evals: EvalStore, eval_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[\w-]+", eval_id) or eval_id not in evals.list():
        raise HTTPException(404, f"No eval {eval_id}")
    meta = evals.meta(eval_id)
    if meta["status"] in ("queued", "running", "stopping") and meta.get("pid") and not _alive(meta["pid"]):
        meta = evals.update(eval_id, status="stopped", current=None, reason="the eval process ended")
    return meta


@app.get("/evals/catalog")
def eval_catalog() -> dict[str, Any]:
    """What can be run: suites, cases and fault profiles."""
    return {
        "suites": [
            {"id": k, **{f: v[f] for f in ("label", "description")}, "pairs": v["pairs"]}
            for k, v in SUITES.items()
        ],
        "cases": [
            {
                "key": c.key,
                "ticket_id": c.ticket_id,
                "summary": c.summary,
                "tags": list(c.tags),
                "approval": c.approval,
            }
            for c in CASES.values()
        ],
        "profiles": [
            {
                "name": p.name,
                "label": p.label,
                "description": p.description,
                "faults": p.faults,
                "applies_to": list(p.applies_to),
            }
            for p in PROFILES.values()
        ],
    }


@app.get("/evals")
def list_evals(evals: Evals) -> list[dict[str, Any]]:
    out = []
    for eval_id in evals.list():
        meta = _eval_meta(evals, eval_id)
        card = scorecard(evals.results(eval_id))
        out.append(
            meta
            | {
                "total": len(meta["pairs"]),
                "done": card["runs"],
                "passed": card["passed"],
                "scored": card["scored"],
                "errors": card["errors"],
                "pass_rate": card["pass_rate"],
            }
        )
    return out


@app.get("/evals/{eval_id}")
def get_eval(eval_id: str, evals: Evals) -> dict[str, Any]:
    meta = _eval_meta(evals, eval_id)
    results = evals.results(eval_id)
    return {"meta": meta, "results": [r.to_json() for r in results], "scorecard": scorecard(results)}


@app.get("/evals/{eval_id}/log")
def eval_log(eval_id: str, evals: Evals, lines: int = 40) -> dict[str, Any]:
    _eval_meta(evals, eval_id)
    path = evals.dir(eval_id) / "log.txt"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return {"lines": text.splitlines()[-lines:]}


class EvalBody(BaseModel):
    suite: str | None = None
    cases: list[str] = []
    profiles: list[str] = []


@app.post("/evals")
def start_eval(body: EvalBody, evals: Evals, launcher: Launcher) -> dict[str, Any]:
    try:
        pairs = resolve_pairs(body.suite, body.cases, body.profiles)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if not pairs:
        raise HTTPException(422, "Nothing to run: no fault profile applies to those cases")
    running = [
        m
        for m in (_eval_meta(evals, i) for i in evals.list())
        if m["status"] in ("queued", "running", "stopping")
    ]
    if running:
        raise HTTPException(
            409, f"Eval {running[0]['id']} is still running; one at a time (they share the sandbox)."
        )
    label = SUITES[body.suite]["label"] if body.suite else f"{len(pairs)} run{'' if len(pairs) == 1 else 's'}"
    eval_id = evals.create(pairs, label=label, models=get_settings().model_list)
    return evals.update(eval_id, pid=launcher(evals.dir(eval_id), eval_id))


@app.post("/evals/{eval_id}/resume")
def resume_eval(eval_id: str, evals: Evals, launcher: Launcher) -> dict[str, Any]:
    meta = _eval_meta(evals, eval_id)
    if meta["status"] in ("queued", "running", "stopping") and _alive(meta.get("pid")):
        raise HTTPException(409, "It is still running")
    evals.update(eval_id, status="queued", reason=None)
    return evals.update(eval_id, pid=launcher(evals.dir(eval_id), eval_id))


@app.post("/evals/{eval_id}/stop")
def stop_eval(eval_id: str, evals: Evals) -> dict[str, Any]:
    """Stop after the run in progress (stopping mid-run would leave the sandbox half-changed)."""
    meta = _eval_meta(evals, eval_id)
    if meta["status"] not in ("queued", "running"):
        raise HTTPException(409, f"It is {meta['status']}")
    return evals.update(eval_id, status="stopping")


def _sandbox_call(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"The sandbox did not answer: {exc}") from None


@app.get("/sandbox/faults")
def sandbox_faults(sandbox: SandboxDep) -> dict[str, Any]:
    """The live sandbox's fault switchboard: its configuration and the faults injected so far."""
    data: dict[str, Any] = _sandbox_call(sandbox.faults)
    return {"config": data["config"], "injected": len(data["log"]), "recent": data["log"][-10:]}


@app.put("/sandbox/faults")
def set_sandbox_faults(config: dict[str, Any], sandbox: SandboxDep) -> dict[str, Any]:
    _sandbox_call(lambda: sandbox.set_faults(config))
    return sandbox_faults(sandbox)


@app.post("/sandbox/reset")
def reset_sandbox(sandbox: SandboxDep) -> dict[str, Any]:
    """Back to the seeded world, faults off. Everything done in the sandbox since is lost."""
    _sandbox_call(sandbox.reset)
    return sandbox_faults(sandbox)


def create_server() -> FastAPI:
    """The API under /api and, when it has been built, the dashboard at /."""
    server = FastAPI(title="Autonomous Company Operator", version=__version__, docs_url="/api/docs")
    server.mount("/api", app)
    dist = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
    if (dist / "index.html").exists():
        server.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @server.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and candidate.parent == dist.resolve():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")  # client-side routes

    return server


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(create_server(), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
