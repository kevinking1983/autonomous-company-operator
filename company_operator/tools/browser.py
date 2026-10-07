"""The operator's browser: a real Chromium driven by Playwright.

Responsibilities that belong to infrastructure rather than to the model:

* **Containment.** Requests may only go to the company's own systems. Every
  non-GET request passes through the policy engine's request guard.
* **Safe retries.** A failed *read* is retried automatically with backoff. A
  failed *write* is never retried blindly: whether it took effect is unknown,
  so it is reported as `uncertain` and the operator must re-read state first.
* **Sessions.** When a system bounces the operator to its login page, the
  session signs in again with the Company Pack's credentials. A read is then
  transparently repeated. A write is reported as `rejected`, because the
  system refused it before saving anything.
* **Perception.** Each page is turned into a compact semantic snapshot with
  refs to interactive elements, plus screenshots for evidence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Request,
    Response,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.policy.engine import GuardVerdict, PolicyEngine
from company_operator.tools.base import ErrorKind

SNAPSHOT_JS = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8")
READ_RETRY_DELAYS = (0.3, 0.8, 1.5)  # seconds between automatic retries of a failed read
NAVIGATION_TIMEOUT_MS = 15_000


@dataclass
class PageState:
    url: str
    title: str
    status: int | None
    snapshot: str
    alerts: list[str] = field(default_factory=list)
    error: ErrorKind | None = None
    note: str = ""

    def render(self) -> str:
        head = [f"URL: {self.url}", f"Title: {self.title}"]
        if self.status is not None:
            head.append(f"HTTP status: {self.status}")
        if self.note:
            head.append(f"Note: {self.note}")
        return "\n".join(head) + "\n\n" + self.snapshot


@dataclass
class _Document:
    method: str
    url: str
    status: int


class BrowserSession:
    def __init__(
        self,
        pack: CompanyPack,
        policy: PolicyEngine,
        log: EventLog,
        base_url: str,
        *,
        headless: bool = True,
        executable_path: str | None = None,
        retry_delays: tuple[float, ...] = READ_RETRY_DELAYS,
    ) -> None:
        self.pack = pack
        self.policy = policy
        self.log = log
        self.base_url = base_url.rstrip("/")
        self.origin = urlsplit(self.base_url).netloc
        self.headless = headless
        self.executable_path = executable_path
        self.retry_delays = retry_delays
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_doc: _Document | None = None
        self._verdicts: list[GuardVerdict] = []

    # ── lifecycle ──

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless, executable_path=self.executable_path
        )
        self._context = await self._browser.new_context(viewport={"width": 1280, "height": 900})
        self._context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        await self._context.route("**/*", self._route)
        self._page = await self._context.new_page()
        self._page.on("response", self._on_response)

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._pw = self._browser = self._context = self._page = None

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def current_url(self) -> str | None:
        return self._page.url if self._page is not None else None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser session not started")
        return self._page

    # ── containment ──

    async def _route(self, route: Route, request: Request) -> None:
        parts = urlsplit(request.url)
        if parts.scheme in ("data", "blob", "about"):
            await route.continue_()
            return
        if parts.netloc != self.origin:
            self.log.emit(
                "guard.blocked",
                method=request.method,
                path=request.url,
                reason="outside the company's systems",
            )
            await route.abort("blockedbyclient")
            return
        if request.method not in ("GET", "HEAD"):
            form = dict(parse_qsl(request.post_data or "", keep_blank_values=True))
            verdict = self.policy.guard(request.method, parts.path, form)
            self._verdicts.append(verdict)
            if not verdict.allowed:
                await route.abort("blockedbyclient")
                return
        await route.continue_()

    def _on_response(self, response: Response) -> None:
        request = response.request
        if (
            self._page is not None
            and request.is_navigation_request()
            and request.frame == self._page.main_frame
        ):
            self._last_doc = _Document(request.method, response.url, response.status)

    # ── navigation and actions ──

    def url_for(self, system: str | None, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        if system:
            # Tolerate the system prefix being repeated in the path ("ops/customers/..." for system "ops").
            prefix = self.pack.systems[system].base_path.strip("/") + "/"
            path = path.lstrip("/")
            if path.startswith(prefix):
                path = path[len(prefix) :]
            base = self.pack.systems[system].url(self.base_url)
            return base.rstrip("/") + "/" + path.lstrip("/") if path not in ("", "/") else base
        return self.base_url + "/" + path.lstrip("/")

    async def open(self, url: str) -> PageState:
        if urlsplit(url).netloc != self.origin:
            return PageState(
                url, "", None, "", error="policy", note="Only the company's own systems can be opened."
            )
        return await self._perform(lambda: self.page.goto(url), is_read=True)

    async def click(self, ref: str) -> PageState:
        locator = self.page.locator(f'[data-aco-ref="{ref}"]')
        if await locator.count() == 0:
            return await self._stale_ref(ref)
        return await self._perform(lambda: locator.click(timeout=5_000), is_read=False)

    async def fill(self, ref: str, value: str) -> PageState:
        locator = self.page.locator(f'[data-aco-ref="{ref}"]')
        if await locator.count() == 0:
            return await self._stale_ref(ref)
        await locator.fill(value, timeout=5_000)
        return await self.state()

    async def select(self, ref: str, option: str) -> PageState:
        locator = self.page.locator(f'[data-aco-ref="{ref}"]')
        if await locator.count() == 0:
            return await self._stale_ref(ref)
        try:
            await locator.select_option(label=option, timeout=3_000)
        except PlaywrightError:
            await locator.select_option(value=option, timeout=3_000)
        return await self.state()

    async def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(path), full_page=True)
        self.log.emit("evidence.saved", kind="screenshot", path=str(path), url=self.page.url)
        return path

    async def fetch_bytes(self, url: str) -> tuple[int, str, bytes]:
        """GET a resource (e.g. an attachment) with the browser's cookies, signing in if needed."""
        for attempt in range(2):
            response = await self.page.request.get(url)
            content_type = response.headers.get("content-type", "")
            if "text/html" in content_type and "/login" in response.url and attempt == 0:
                system = self._system_of(response.url)
                if system:
                    await self.open(self.url_for(system, "login"))
                    continue
            return response.status, content_type, await response.body()
        return 401, "", b""

    async def state(self, note: str = "", error: ErrorKind | None = None) -> PageState:
        page = self.page
        for attempt in range(3):  # a navigation may still be settling
            try:
                await page.wait_for_load_state("load")
                snapshot = await page.evaluate(SNAPSHOT_JS, {"maxRows": 40, "maxLines": 400})
                break
            except PlaywrightError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.2)
        alerts = await page.locator("[role=alert]").all_inner_texts()
        return PageState(
            url=page.url,
            title=await page.title(),
            status=self._last_doc.status if self._last_doc else None,
            snapshot=snapshot,
            alerts=[a.strip() for a in alerts],
            error=error,
            note=note,
        )

    # ── the core: perform an action and classify what happened ──

    async def _perform(self, action: Callable[[], Awaitable[object]], *, is_read: bool) -> PageState:
        self._last_doc = None
        self._verdicts = []
        try:
            await action()
            await self.page.wait_for_load_state("load")
        except PlaywrightError as exc:
            if any(not v.allowed for v in self._verdicts):
                return await self._blocked()
            message = str(exc).splitlines()[0]
            self.log.emit("browser.error", error=message, url=self.page.url)
            return await self.state(note=f"Browser error: {message}", error="transient")

        if any(not v.allowed for v in self._verdicts):
            return await self._blocked()

        doc = self._last_doc
        verdicts = list(self._verdicts)
        # Did this action send a form (a non-GET request)? It may have been followed by a redirect.
        submitted = bool(verdicts)

        # Bounced to a login page: the session expired.
        system = self._system_of(self.page.url)
        if system and urlsplit(self.page.url).path.rstrip("/").endswith("/login"):
            await self._login(system)
            if submitted:
                self._release_grants(verdicts)
                return await self.state(
                    note="The session had expired, so the system rejected the form before saving anything. "
                    "You are signed in again; repeat the action.",
                    error="rejected",
                )
            return await self.state(note=f"Signed in to {system}.")

        if doc is None:
            return await self.state()

        if doc.status >= 500:
            if doc.method == "GET":  # reads are safe to repeat (incl. the page after a successful redirect)
                return await self._retry_read(doc)
            self.log.emit("browser.uncertain", method=doc.method, url=doc.url, status=doc.status)
            return await self.state(
                note=f"The system answered {doc.status} to a form submission. The change may or may not have "
                "been saved. Check the current state of the record before trying again.",
                error="uncertain",
            )
        if doc.status == 404:
            return await self.state(note="Page or record not found.", error="not_found")
        if 400 <= doc.status < 500:
            self._release_grants(verdicts)
            return await self.state(
                note=f"The system rejected the request ({doc.status}); nothing was saved.", error="rejected"
            )

        current = await self.state()
        if submitted and current.alerts:  # role=alert after a submission: the system refused it
            self._release_grants(verdicts)
            current.error = "rejected"
            current.note = "The system refused the submission: " + " / ".join(current.alerts)
        return current

    async def _retry_read(self, doc: _Document) -> PageState:
        for attempt, delay in enumerate(self.retry_delays, start=1):
            self.log.emit("browser.retry", url=doc.url, status=doc.status, attempt=attempt, delay=delay)
            await asyncio.sleep(delay)
            self._last_doc = None
            try:
                await self.page.goto(doc.url)
            except PlaywrightError:
                continue
            if self._last_doc and self._last_doc.status < 500:
                result = await self.state(
                    note=f"Recovered after {attempt} automatic retr{'y' if attempt == 1 else 'ies'}."
                )
                return result
        return await self.state(
            note=f"The page kept failing ({doc.status}) after {len(self.retry_delays)} retries.",
            error="transient",
        )

    async def _login(self, system: str) -> None:
        credentials = self.pack.systems[system].credentials
        self.log.emit("browser.login", system=system, username=credentials.username)
        page = self.page
        await page.get_by_label("Username").fill(credentials.username)
        await page.get_by_label("Password").fill(credentials.password())
        await page.get_by_role("button", name="Sign in").click()
        await page.wait_for_load_state("load")
        if urlsplit(page.url).path.rstrip("/").endswith("/login"):
            raise PermissionError(f"Could not sign in to {system} as {credentials.username}")

    async def _blocked(self) -> PageState:
        reasons = [v.reason for v in self._verdicts if not v.allowed]
        # A blocked form submission leaves the browser on its own error page; go back to where we were.
        await asyncio.sleep(0.1)
        if self.page.url.startswith("chrome-error://"):
            await self.page.go_back(wait_until="load")
        return await self.state(note="Blocked by company policy: " + "; ".join(reasons), error="policy")

    async def _stale_ref(self, ref: str) -> PageState:
        return await self.state(
            note=f"No element {ref} on the current page (the page changed). Use the refs in this snapshot.",
            error="not_found",
        )

    def _release_grants(self, verdicts: list[GuardVerdict]) -> None:
        for verdict in verdicts:
            if verdict.grant_id:
                self.policy.release(verdict.grant_id)

    def _system_of(self, url: str) -> str | None:
        first = urlsplit(url).path.strip("/").split("/", 1)[0]
        return first if first in self.pack.systems else None
