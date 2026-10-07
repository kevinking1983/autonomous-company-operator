"""Assemble everything one run needs to act: event log, policy, browser, human channel."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.config import Settings
from company_operator.memory.store import OperatorDB
from company_operator.policy import PolicyEngine
from company_operator.tools import ToolContext
from company_operator.tools.browser import BrowserSession
from company_operator.tools.human import HumanChannel


@asynccontextmanager
async def tool_environment(
    pack: CompanyPack, settings: Settings, run_dir: Path, db: OperatorDB | None = None
) -> AsyncIterator[ToolContext]:
    """A ToolContext whose audit log lives in the run's directory. The browser is closed on exit.

    Requests to people go to `db`, so a paused run can be resumed later in a
    new environment, even by another process.
    """
    log = EventLog(run_dir / "events.jsonl")
    policy = PolicyEngine(pack, log)
    browser = BrowserSession(
        pack,
        policy,
        log,
        settings.sandbox_url,
        headless=settings.browser_headless,
        executable_path=settings.browser_executable,
    )
    async with browser:
        yield ToolContext(
            pack=pack,
            policy=policy,
            log=log,
            run_dir=run_dir,
            browser=browser,
            human=HumanChannel(policy, log, db, run_id=run_dir.name),
        )
