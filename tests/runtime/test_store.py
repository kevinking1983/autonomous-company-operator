import json
from pathlib import Path

from company_operator.company_pack import CompanyPack
from company_operator.config import Settings
from company_operator.runtime import RunStore, TaskRequest
from company_operator.runtime.environment import tool_environment
from tests.conftest import LiveSandbox


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    state = store.create(TaskRequest(text="Resolve TKT-1001", source="ticket", ticket_id="TKT-1001"))
    state.memory["payment_id"] = "PAY-90012"
    state.phase = "execute"
    store.save(state)
    loaded = store.load(state.run_id)
    assert loaded.memory == {"payment_id": "PAY-90012"} and loaded.phase == "execute"
    assert store.list() == [state.run_id]
    assert not list(store.run_dir(state.run_id).glob("*.tmp"))  # atomic replace leaves no temp file


async def test_tool_environment_logs_into_the_run_directory(
    tmp_path: Path, pack: CompanyPack, sandbox: LiveSandbox
) -> None:
    settings = Settings(sandbox_url=sandbox.url)
    async with tool_environment(pack, settings, tmp_path) as ctx:
        state = await ctx.browser.open(ctx.browser.url_for("support", "tickets"))
        assert state.error is None
    events = [json.loads(line)["type"] for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert "browser.login" in events
