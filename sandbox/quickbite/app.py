"""QuickBite sandbox server: three back-office apps plus a control API.

/support/*   Support Desk         (operator-facing)
/ops/*       Ops Admin            (operator-facing)
/payments/*  Payments Console     (operator-facing)
/_control/*  reset, fault injection, scripted customers (test harness only;
             requires the X-Control-Key header and is never given to the operator)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sandbox.quickbite import ops, payments, support
from sandbox.quickbite.db import Database, now_iso
from sandbox.quickbite.faults import FaultConfig, FaultInjector
from sandbox.quickbite.seed import SCENARIOS, seed
from sandbox.quickbite.web import LoginRequired, Sandbox, make_templates

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sandbox" / "quickbite.db"
DEFAULT_CONTROL_KEY = "sandbox-control"


def create_app(db_path: Path | None = None, control_key: str | None = None, reset: bool = True) -> FastAPI:
    db = Database(db_path or Path(os.environ.get("QUICKBITE_DB", DEFAULT_DB_PATH)))
    key = control_key or os.environ.get("QUICKBITE_CONTROL_KEY", DEFAULT_CONTROL_KEY)
    if reset or not db.path.exists():
        seed(db)

    app = FastAPI(title="QuickBite sandbox", docs_url="/_control/docs", openapi_url="/_control/openapi.json")
    faults = FaultInjector()
    app.state.sandbox = Sandbox(db=db, faults=faults, templates=make_templates())

    def expire_all_sessions() -> None:
        systems = faults.config.systems
        db.execute(f"DELETE FROM staff_sessions WHERE system IN ({','.join('?' * len(systems))})", *systems)

    app.middleware("http")(faults.middleware(expire_all_sessions))
    app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")
    app.include_router(support.router)
    app.include_router(ops.router)
    app.include_router(payments.router)

    @app.exception_handler(LoginRequired)
    async def to_login(request: Request, exc: LoginRequired) -> Response:
        return RedirectResponse(f"/{exc.system}/login?next={quote(exc.next_path)}", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_PAGE

    app.include_router(control_router(key))
    return app


# ───────────────────────── control API ─────────────────────────


class CustomerMessage(BaseModel):
    body: str


def control_router(key: str) -> APIRouter:
    def require_key(x_control_key: Annotated[str | None, Header()] = None) -> None:
        if x_control_key != key:
            raise HTTPException(403, "Control API requires a valid X-Control-Key header")

    router = APIRouter(prefix="/_control", dependencies=[Depends(require_key)], tags=["control"])

    @router.post("/reset")
    def reset_world(request: Request) -> dict[str, str]:
        sandbox: Sandbox = request.app.state.sandbox
        seed(sandbox.db)
        sandbox.faults.reset()
        return {"status": "reset", "at": now_iso()}

    @router.get("/faults")
    def get_faults(request: Request) -> dict[str, object]:
        snapshot: dict[str, object] = request.app.state.sandbox.faults.snapshot()
        return snapshot

    @router.put("/faults")
    def set_faults(request: Request, config: FaultConfig) -> dict[str, object]:
        faults: FaultInjector = request.app.state.sandbox.faults
        faults.configure(config)
        return faults.snapshot()

    @router.get("/scenarios")
    def scenarios() -> list[dict[str, object]]:
        return [vars(s) | {"tags": list(s.tags)} for s in SCENARIOS.values()]

    @router.post("/tickets/{ticket_id}/customer-message")
    def customer_message(request: Request, ticket_id: str, message: CustomerMessage) -> dict[str, str]:
        """Simulate the customer writing back on a ticket."""
        db: Database = request.app.state.sandbox.db
        with db.connect() as conn:
            ticket = conn.execute(
                "SELECT requester_name FROM support_tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            if ticket is None:
                raise HTTPException(404, f"Ticket {ticket_id} not found")
            now = now_iso()
            conn.execute(
                "INSERT INTO support_messages (ticket_id, author_type, author, body, at) VALUES (?, 'customer', ?, ?, ?)",
                (ticket_id, ticket["requester_name"], message.body, now),
            )
            conn.execute(
                "UPDATE support_tickets SET status = 'open', updated_at = ? WHERE id = ?", (now, ticket_id)
            )
        return {"status": "delivered", "ticket_id": ticket_id}

    @router.get("/state/{table}")
    def dump_table(request: Request, table: str) -> list[dict[str, object]]:
        """Ground truth for the eval harness: raw rows of one table (binary columns omitted)."""
        db: Database = request.app.state.sandbox.db
        known = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if table not in known:
            raise HTTPException(404, f"Unknown table {table}")
        return [
            {k: row[k] for k in row.keys() if not isinstance(row[k], bytes)}  # noqa: SIM118 - Row iterates values
            for row in db.all(f"SELECT * FROM {table}")
        ]

    return router


INDEX_PAGE = """<!doctype html><html lang="en"><head><title>QuickBite internal tools</title>
<link rel="stylesheet" href="/static/sandbox.css"></head><body><main>
<h1>QuickBite internal tools (sandbox)</h1>
<ul>
  <li><a href="/support/">Support Desk</a>: customer tickets</li>
  <li><a href="/ops/">Ops Admin</a>: orders, restaurants, riders, incidents</li>
  <li><a href="/payments/">Payments Console</a>: payments, refunds, coupons</li>
</ul>
<p class="muted">Fictional company. No real customers, data or money.</p>
</main></body></html>"""


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the QuickBite sandbox.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--keep-data", action="store_true", help="Do not reset the world on startup.")
    args = parser.parse_args()
    uvicorn.run(create_app(reset=not args.keep_data), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
