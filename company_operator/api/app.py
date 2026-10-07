"""Operator API: health, the approval inbox, questions, and the operator's memory.

The dashboard (step 10) is built on these endpoints. Runs themselves are
started and resumed by the worker (step 9) or the operator-run CLI.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from company_operator import __version__
from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack, load_pack
from company_operator.config import get_settings
from company_operator.memory.store import OperatorDB
from company_operator.policy import PolicyEngine
from company_operator.tools.human import HumanChannel

app = FastAPI(title="Autonomous Company Operator", version=__version__)


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


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
