"""Shared web plumbing for the three QuickBite apps: sessions, login, rendering."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from sandbox.quickbite.db import Database, fmt_time, minutes_between, now_iso, rupees
from sandbox.quickbite.faults import FaultInjector

TEMPLATES_DIR = Path(__file__).with_name("templates")

SYSTEM_TITLES = {
    "support": "QuickBite Support Desk",
    "ops": "QuickBite Ops Admin",
    "payments": "QuickBite Payments Console",
}


@dataclass
class Sandbox:
    db: Database
    faults: FaultInjector
    templates: Jinja2Templates


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.globals.update(rupees=rupees, fmt_time=fmt_time, minutes_between=minutes_between)
    return templates


def sandbox_of(request: Request) -> Sandbox:
    sandbox: Sandbox = request.app.state.sandbox
    return sandbox


class LoginRequired(Exception):
    def __init__(self, system: str, next_path: str) -> None:
        self.system = system
        self.next_path = next_path


def cookie_name(system: str) -> str:
    return f"qb_{system}_session"


def current_user(request: Request, system: str) -> dict[str, Any]:
    """The logged-in staff member for this system, or a redirect to its login page."""
    token = request.cookies.get(cookie_name(system))
    row = None
    if token:
        row = sandbox_of(request).db.one(
            """SELECT u.username, u.display_name, u.role FROM staff_sessions s
               JOIN staff_users u ON u.username = s.username AND u.system = s.system
               WHERE s.token = ? AND s.system = ?""",
            token,
            system,
        )
    if row is None:
        if request.method == "GET":
            next_path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        else:
            # A form submitted after the session expired: nothing is saved, and after
            # signing in the user returns to the page the form was on.
            referer = urlsplit(request.headers.get("referer", ""))
            next_path = referer.path if referer.path.startswith(f"/{system}/") else f"/{system}/"
            if referer.query and next_path != f"/{system}/":
                next_path += f"?{referer.query}"
        raise LoginRequired(system, next_path)
    return dict(row)


def render(
    request: Request, system: str, template: str, user: dict[str, Any] | None = None, **context: Any
) -> HTMLResponse:
    sandbox = sandbox_of(request)
    status_code = context.pop("status_code", 200)
    error = context.pop("error", None) or request.query_params.get("error")
    return sandbox.templates.TemplateResponse(
        request,
        template,
        {
            "system": system,
            "system_title": SYSTEM_TITLES[system],
            "user": user,
            "layout": sandbox.faults.layout_for(system),
            "msg": request.query_params.get("msg"),
            "error": error,
            **context,
        },
        status_code=status_code,
    )


def redirect(path: str, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    if msg:
        path += ("&" if "?" in path else "?") + "msg=" + quote(msg)
    if error:
        path += ("&" if "?" in path else "?") + "error=" + quote(error)
    return RedirectResponse(path, status_code=303)


def login_router(system: str) -> APIRouter:
    """Login and logout pages for one system. Each system has its own accounts and session cookie."""
    router = APIRouter()

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "") -> Response:
        return render(request, system, "login.html", next=next)

    @router.post("/login")
    def login(
        request: Request, username: str = Form(), password: str = Form(), next: str = Form("")
    ) -> Response:
        db = sandbox_of(request).db
        user = db.one(
            "SELECT username FROM staff_users WHERE username = ? AND system = ? AND password = ?",
            username.strip(),
            system,
            password,
        )
        if user is None:
            return render(
                request,
                system,
                "login.html",
                next=next,
                error="Invalid username or password.",
                status_code=401,
            )
        token = secrets.token_urlsafe(24)
        db.execute(
            "INSERT INTO staff_sessions (token, username, system, created_at) VALUES (?, ?, ?, ?)",
            token,
            user["username"],
            system,
            now_iso(),
        )
        target = next if next.startswith(f"/{system}/") else f"/{system}/"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(cookie_name(system), token, httponly=True, samesite="lax", path=f"/{system}")
        return response

    @router.post("/logout")
    def logout(request: Request) -> Response:
        token = request.cookies.get(cookie_name(system))
        if token:
            sandbox_of(request).db.execute("DELETE FROM staff_sessions WHERE token = ?", token)
        response = redirect(f"/{system}/login", msg="You have been signed out.")
        response.delete_cookie(cookie_name(system), path=f"/{system}")
        return response

    return router
