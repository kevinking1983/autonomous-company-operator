import re
from typing import Any

from company_operator.tools import ToolContext, ToolRegistry, ToolResult


def ref(result: ToolResult, line_start: str) -> str:
    """The [eN] ref on the first snapshot line that starts with `line_start` (ignoring indentation)."""
    for line in result.output.splitlines():
        if line.strip().startswith(line_start):
            found = re.search(r"\[(e\d+)", line)
            if found:
                return found.group(1)
    raise AssertionError(f"No line starting with {line_start!r} in snapshot:\n{result.output}")


class Driver:
    """Calls tools the way the runtime will, keeping the latest page result."""

    def __init__(self, registry: ToolRegistry, ctx: ToolContext) -> None:
        self.registry = registry
        self.ctx = ctx
        self.page: ToolResult | None = None

    async def call(self, tool: str, **arguments: Any) -> ToolResult:
        result = await self.registry.call(self.ctx, tool, arguments)
        if result.output.startswith("URL: "):  # keep the latest page snapshot
            self.page = result
        return result

    def ref(self, line_start: str) -> str:
        assert self.page is not None
        return ref(self.page, line_start)

    async def open(self, system: str, path: str) -> ToolResult:
        return await self.call("browser_open", system=system, path=path)

    async def fill(self, line_start: str, value: str) -> ToolResult:
        return await self.call("browser_fill", ref=self.ref(line_start), value=value)

    async def select(self, line_start: str, option: str) -> ToolResult:
        return await self.call("browser_select", ref=self.ref(line_start), option=option)

    async def click(self, line_start: str, action: str | None = None, **facts: Any) -> ToolResult:
        args: dict[str, Any] = {"ref": self.ref(line_start)}
        if action:
            args |= {"action": action, "facts": facts}
        return await self.call("browser_click", **args)
