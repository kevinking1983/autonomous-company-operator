from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack, load_pack
from company_operator.config import Settings
from company_operator.policy import PolicyEngine
from company_operator.tools import ToolContext, ToolRegistry, default_registry
from company_operator.tools.browser import BrowserSession
from company_operator.tools.human import HumanChannel
from tests.conftest import LiveSandbox


@pytest.fixture(scope="session")
def pack() -> CompanyPack:
    return load_pack(Settings().company_pack_dir)


@pytest.fixture
def registry() -> ToolRegistry:
    return default_registry()


@pytest.fixture
async def ctx(pack: CompanyPack, sandbox: LiveSandbox, tmp_path: Path) -> AsyncIterator[ToolContext]:
    settings = Settings()
    log = EventLog(tmp_path / "events.jsonl")
    policy = PolicyEngine(pack, log)
    browser = BrowserSession(
        pack,
        policy,
        log,
        sandbox.url,
        executable_path=settings.browser_executable,
        retry_delays=(0.05, 0.1, 0.2),
    )
    async with browser:
        yield ToolContext(
            pack=pack,
            policy=policy,
            log=log,
            run_dir=tmp_path,
            browser=browser,
            human=HumanChannel(policy, log),
        )
