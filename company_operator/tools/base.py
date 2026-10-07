"""Tool framework: typed inputs, structured results, one registry.

A tool is the only way the operator touches the world. Each tool declares a
Pydantic input model (which becomes the JSON schema the model sees) and
returns a ToolResult whose `error` field classifies failures. The runtime
reacts differently to each kind of failure.
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.policy.engine import PolicyEngine

if TYPE_CHECKING:
    from company_operator.queue.tasks import TaskQueue
    from company_operator.tools.browser import BrowserSession
    from company_operator.tools.human import HumanChannel

# How a failure should be handled:
#   transient      infrastructure hiccup that persisted after automatic retries; try again later
#   uncertain      a write was sent but the outcome is unknown; re-read state BEFORE retrying
#   rejected       the system refused the request and nothing changed; fix the input or reload and retry
#   not_found      the page, record or element does not exist (or the page changed); re-observe
#   policy         blocked or denied by company policy; do not work around it
#   needs_approval policy requires a human first; request approval
#   invalid_input  the tool was called with bad arguments
ErrorKind = Literal[
    "transient", "uncertain", "rejected", "not_found", "policy", "needs_approval", "invalid_input"
]


LIVE_FRAME = "live.jpg"  # the latest browser frame, for watching a run as it happens


@dataclass
class ToolResult:
    ok: bool
    output: str
    error: ErrorKind | None = None
    data: dict[str, Any] = field(default_factory=dict)
    evidence: list[Path] = field(default_factory=list)  # screenshots and files saved for the run report
    images: list[Path] = field(default_factory=list)  # images the model should look at

    @classmethod
    def success(cls, output: str, **kwargs: Any) -> ToolResult:
        return cls(ok=True, output=output, **kwargs)

    @classmethod
    def failure(cls, error: ErrorKind, output: str, **kwargs: Any) -> ToolResult:
        return cls(ok=False, output=output, error=error, **kwargs)


@dataclass
class ToolContext:
    pack: CompanyPack
    policy: PolicyEngine
    log: EventLog
    run_dir: Path
    browser: BrowserSession
    human: HumanChannel
    # Set when the run is executed by a queue worker: lets the operator delegate sub-tasks.
    queue: TaskQueue | None = None
    task_id: str | None = None

    @property
    def evidence_dir(self) -> Path:
        path = self.run_dir / "evidence"
        path.mkdir(parents=True, exist_ok=True)
        return path


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tool[InputT: ToolInput](ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[ToolInput]]

    @abstractmethod
    async def run(self, ctx: ToolContext, args: InputT) -> ToolResult: ...

    def schema(self) -> dict[str, Any]:
        """Provider-neutral tool definition: name, description and JSON schema of the input."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool[Any]]) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Duplicate tool {tool.name}")
            self._tools[tool.name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    async def call(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Validate arguments, run the tool and log both sides of the call."""
        ctx.log.emit("tool.call", tool=name, arguments=arguments)
        tool = self._tools.get(name)
        if tool is None:
            result = ToolResult.failure(
                "invalid_input", f"Unknown tool {name!r}. Available: {', '.join(self._tools)}"
            )
        else:
            try:
                args = tool.input_model.model_validate(arguments)
            except ValidationError as exc:
                result = ToolResult.failure("invalid_input", f"Invalid arguments for {name}: {exc}")
            else:
                try:
                    result = await tool.run(ctx, args)
                except Exception as exc:  # a tool bug must not crash the run; it becomes an observation
                    result = ToolResult.failure(
                        "transient", f"{name} failed unexpectedly: {type(exc).__name__}: {exc}"
                    )
        if name.startswith("browser_"):
            # The live view is a convenience for people watching; it never fails a run.
            with contextlib.suppress(Exception):
                await ctx.browser.live_frame(ctx.run_dir / LIVE_FRAME)
        ctx.log.emit(
            "tool.result",
            tool=name,
            ok=result.ok,
            error=result.error,
            output=result.output[:2000],
            data=result.data,
            evidence=[str(p) for p in result.evidence],
        )
        return result
