import pytest

from company_operator.audit.log import EventLog
from company_operator.tools import ToolContext, ToolRegistry, default_registry
from company_operator.tools.library import ClickInput


def test_schemas_are_provider_neutral() -> None:
    schemas = {s["name"]: s for s in default_registry().schemas()}
    click = schemas["browser_click"]["input_schema"]
    assert click["required"] == ["ref"]
    assert {"ref", "action", "facts"} <= set(click["properties"])
    assert len(schemas) == 12


async def test_unknown_tool_and_bad_arguments(registry: ToolRegistry, ctx: ToolContext) -> None:
    assert (await registry.call(ctx, "rm_rf", {})).error == "invalid_input"
    bad = await registry.call(ctx, "browser_click", {"reference": "e1"})
    assert bad.error == "invalid_input" and "ref" in bad.output
    assert isinstance(ctx.log, EventLog)
    assert [e.type for e in ctx.log.events][-4:] == ["tool.call", "tool.result", "tool.call", "tool.result"]


@pytest.mark.parametrize("written", ["e12", "[e12]", " [e12 -> /support/tickets] ", "ref e12"])
def test_refs_are_accepted_the_way_models_write_them(written: str) -> None:
    # Regression: a model wrote "[e5]" (as refs appear in snapshots); every fill failed as "page changed".
    assert ClickInput(ref=written).ref == "e12"
