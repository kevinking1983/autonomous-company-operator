"""Operator API: health, the approval inbox, questions, and the operator's memory.

The dashboard (step 10) is built on these endpoints. Runs themselves are
started and resumed by the worker (step 9) or the operator-run CLI.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from company_operator import __version__
from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack, load_pack
from company_operator.config import get_settings
from company_operator.memory.store import OperatorDB
from company_operator.policy import PolicyEngine
from company_operator.queue import PRIORITY, TaskQueue
from company_operator.runtime import RunStore
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


@app.post("/requests/{request_id}/decision")
def decide(
    request_id: str,
    body: DecisionBody,
    db: Db,
    pack: Pack,
) -> dict[str, Any]:
    try:
        request = channel(db, pack).decide(request_id, body.approved, body.approver, body.note, body.remember)
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
def get_run(run_id: str, store: Store) -> dict[str, Any]:
    _run_dir(store, run_id)
    return store.load(run_id).model_dump()


@app.get("/runs/{run_id}/report")
def get_report(run_id: str, store: Store) -> dict[str, Any]:
    run_dir = _run_dir(store, run_id)
    return build_report(store.load(run_id), load_events(run_dir), run_dir)


@app.get("/runs/{run_id}/events")
def get_events(run_id: str, store: Store, after: int = 0) -> list[dict[str, Any]]:
    return [e for e in load_events(_run_dir(store, run_id)) if e["seq"] > after]


@app.get("/runs/{run_id}/stream")
async def stream_events(run_id: str, store: Store, after: int = 0) -> StreamingResponse:
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
def evidence(run_id: str, name: str, store: Store) -> FileResponse:
    folder = _run_dir(store, run_id) / "evidence"
    path = (folder / name).resolve()
    if path.parent != folder.resolve() or not path.is_file():  # no ../ escapes
        raise HTTPException(404, f"No evidence {name}")
    return FileResponse(path)


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
