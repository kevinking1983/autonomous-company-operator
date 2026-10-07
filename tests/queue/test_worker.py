"""Workers seeing tasks through: running, pausing for people, crashing, and delegating."""

from datetime import timedelta
from pathlib import Path

import pytest

from company_operator.company_pack import CompanyPack
from company_operator.config import Settings
from company_operator.memory.store import OperatorDB
from company_operator.queue import TaskQueue
from company_operator.queue.worker import Worker
from company_operator.runtime import BrainContext, NextAction, Operator, Plan, RunStore, UnderstandDecision
from company_operator.tools import ToolRegistry, default_registry
from company_operator.tools.human import HumanChannel
from tests.conftest import LiveSandbox
from tests.runtime.scripted import ScriptedBrain, ScriptedVerifier, done, open_, plan, tool


@pytest.fixture
def db(tmp_path: Path) -> OperatorDB:
    return OperatorDB(tmp_path / "operator.db")


@pytest.fixture
def settings(sandbox: LiveSandbox, tmp_path: Path) -> Settings:
    return Settings(sandbox_url=sandbox.url, data_dir=tmp_path)


class RoutingBrain:
    """One scripted brain per request text, as if each task got its own reasoning."""

    def __init__(self, brains: dict[str, ScriptedBrain]) -> None:
        self.brains = brains

    def _for(self, ctx: BrainContext) -> ScriptedBrain:
        return self.brains[ctx.state.request.text]

    async def understand(self, ctx: BrainContext) -> UnderstandDecision:
        return await self._for(ctx).understand(ctx)

    async def plan(self, ctx: BrainContext) -> Plan:
        return await self._for(ctx).plan(ctx)

    async def next_action(self, ctx: BrainContext) -> NextAction:
        return await self._for(ctx).next_action(ctx)

    async def summarize(self, ctx: BrainContext) -> str:
        return f"{ctx.state.request.text}: done"


def make_worker(
    brain: object, pack: CompanyPack, db: OperatorDB, settings: Settings, queue: TaskQueue | None = None
) -> Worker:
    operator = Operator(
        brain,
        ScriptedVerifier(True),
        default_registry(),
        pack,
        RunStore(settings.runs_dir),
        db=db,
        transient_backoff=0,
    )  # type: ignore[arg-type]
    return Worker(operator, queue or TaskQueue(db), settings, name="w1", check_interval=timedelta(seconds=-1))


async def test_worker_runs_a_task_to_completion(
    pack: CompanyPack, db: OperatorDB, settings: Settings, registry: ToolRegistry
) -> None:
    queue = TaskQueue(db)
    queue.enqueue("Resolve support ticket TKT-1001.", ticket_id="TKT-1001", source="ticket")
    worker = make_worker(
        ScriptedBrain(moves=[open_("support", "tickets/TKT-1001", step="s1")]), pack, db, settings, queue
    )
    finished = await worker.run_once()
    assert finished is not None and finished.status == "completed" and finished.run_id
    assert (settings.runs_dir / finished.run_id / "report.md").exists()
    assert await worker.run_once() is None  # nothing left


async def test_paused_task_resumes_after_approval(
    pack: CompanyPack, db: OperatorDB, settings: Settings, sandbox: LiveSandbox
) -> None:
    facts = {"payment_id": "PAY-1", "amount": 900, "reason": "wrong_order"}
    brain = ScriptedBrain(
        plans=[plan("refund")],
        moves=[
            tool(
                "request_approval", "refund", action="payments.refund", facts=facts, justification="Bag swap"
            ),
            done("refund", "ok"),
        ],
    )
    queue = TaskQueue(db)
    task = queue.enqueue("Resolve support ticket TKT-1003.", ticket_id="TKT-1003")
    worker = make_worker(brain, pack, db, settings, queue)

    assert (await worker.run_once()).status == "waiting"  # type: ignore[union-attr]
    assert (await worker.run_once()).status == "waiting"  # type: ignore[union-attr]  # checked again: still undecided
    [request] = db.list_requests(status="pending")
    from company_operator.audit.log import EventLog
    from company_operator.policy import PolicyEngine

    HumanChannel(PolicyEngine(pack, EventLog()), EventLog(), OperatorDB(db.path)).decide(
        request.id, True, "priya.supervisor"
    )
    assert (await worker.run_once()).status == "completed"  # type: ignore[union-attr]
    assert queue.get(task.id).status == "completed"


async def test_crashed_task_resumes_from_its_checkpoint(
    pack: CompanyPack, db: OperatorDB, settings: Settings, sandbox: LiveSandbox
) -> None:
    store = RunStore(settings.runs_dir)
    brain = ScriptedBrain(moves=[open_("support", "tickets/TKT-1001", step="s1")])
    operator = Operator(brain, ScriptedVerifier(True), default_registry(), pack, store, db=db)
    queue = TaskQueue(db, lease=timedelta(seconds=-1))
    task = queue.enqueue("Resolve support ticket TKT-1001.", ticket_id="TKT-1001")
    # A first worker claimed it, got as far as a plan, checkpointed, and died.
    claimed = queue.claim("dead-worker")
    assert claimed is not None
    from company_operator.runtime import TaskRequest
    from tests.runtime.scripted import contract

    state = store.create(TaskRequest(text=task.text, ticket_id="TKT-1001"))
    state.contract, state.phase = contract("done"), "plan"
    store.save(state)
    queue.attach_run(task.id, state.run_id)

    survivor = Worker(
        operator, TaskQueue(db), settings, name="survivor", check_interval=timedelta(seconds=-1)
    )
    finished = await survivor.run_once()
    assert finished is not None and finished.status == "completed" and finished.run_id == state.run_id
    assert brain.understand_calls == 0  # it continued from the checkpoint, it did not start over


async def test_batch_request_delegates_and_waits_for_sub_tasks(
    pack: CompanyPack, db: OperatorDB, settings: Settings, sandbox: LiveSandbox
) -> None:
    parent_text = "Clear today's late-delivery tickets TKT-1015 and TKT-1016."
    brains = {
        parent_text: ScriptedBrain(
            plans=[plan("delegate")],
            moves=[
                tool(
                    "delegate_tasks",
                    "delegate",
                    about="late deliveries",
                    tasks=[
                        {"text": "Resolve support ticket TKT-1015.", "ticket_id": "TKT-1015"},
                        {"text": "Resolve support ticket TKT-1016.", "ticket_id": "TKT-1016"},
                    ],
                ),
                done("delegate", "both handled"),
            ],
        ),
        "Resolve support ticket TKT-1015.": ScriptedBrain(
            moves=[open_("support", "tickets/TKT-1015", step="s1")]
        ),
        "Resolve support ticket TKT-1016.": ScriptedBrain(
            moves=[open_("support", "tickets/TKT-1016", step="s1")]
        ),
    }
    queue = TaskQueue(db)
    parent = queue.enqueue(parent_text, source="supervisor", requested_by="priya.supervisor")
    worker = make_worker(RoutingBrain(brains), pack, db, settings, queue)

    assert (await worker.run_once()).status == "waiting"  # type: ignore[union-attr]  # parent delegated, now waits
    children = queue.list(parent_id=parent.id)
    assert sorted(c.ticket_id or "" for c in children) == ["TKT-1015", "TKT-1016"]
    while (await worker.run_once()) is not None:
        pass
    assert queue.get(parent.id).status == "completed"
    assert all(c.status == "completed" for c in queue.list(parent_id=parent.id))


class RoutingVerifier:
    def __init__(self, verdicts: dict[str, bool]) -> None:
        self.verdicts = verdicts

    async def verify(self, state: object, tools: object) -> object:
        from company_operator.runtime import CriterionResult, RunState, Verification

        assert isinstance(state, RunState) and state.contract is not None
        passed = self.verdicts.get(state.request.text, True)
        return Verification(
            passed=passed,
            results=[
                CriterionResult(criterion_id=c.id, passed=passed, evidence="scripted")
                for c in state.contract.success_criteria
            ],
        )


async def test_parent_leaves_an_escalated_sub_task_to_a_person(
    pack: CompanyPack, db: OperatorDB, settings: Settings, sandbox: LiveSandbox
) -> None:
    """Option B: when a sub-task escalates, the parent may not take that ticket back; it reports it."""
    from tests.runtime.scripted import click, contract, select

    parent_text = "Clear late-delivery tickets TKT-1015 and TKT-1016."
    parent = ScriptedBrain(
        task=contract("Both tickets resolved"),
        plans=[plan("delegate"), plan("report")],
        moves=[
            tool("delegate_tasks", "delegate", about="late deliveries", tasks=[
                {"text": "Resolve support ticket TKT-1015.", "ticket_id": "TKT-1015"},
                {"text": "Resolve support ticket TKT-1016.", "ticket_id": "TKT-1016"},
            ]),
            # After resuming, the parent tries to fix TKT-1016 itself (what happened in the live run)...
            open_("support", "tickets/TKT-1016", step="report"),
            select('combobox "Status"', "resolved"),
            click('button "Update ticket"', "support.update_ticket", ticket_id="TKT-1016"),
            done("report", "TKT-1015 resolved; TKT-1016 left for a person"),
        ],
    )  # fmt: skip
    brains = {
        parent_text: parent,
        "Resolve support ticket TKT-1015.": ScriptedBrain(
            moves=[open_("support", "tickets/TKT-1015", step="s1")]
        ),
        "Resolve support ticket TKT-1016.": ScriptedBrain(
            moves=[open_("support", "tickets/TKT-1016", step="s1")]
        ),
    }
    operator = Operator(
        RoutingBrain(brains),  # type: ignore[arg-type]
        RoutingVerifier({"Resolve support ticket TKT-1016.": False}),  # type: ignore[arg-type]
        default_registry(), pack, RunStore(settings.runs_dir), db=db, transient_backoff=0,
    )  # fmt: skip
    queue = TaskQueue(db)
    task = queue.enqueue(parent_text, source="supervisor")
    worker = Worker(operator, queue, settings, name="w1", check_interval=timedelta(seconds=-1))
    while (await worker.run_once()) is not None:
        pass

    children = {c.ticket_id: c.status for c in queue.list(parent_id=task.id)}
    assert children == {"TKT-1015": "completed", "TKT-1016": "escalated"}
    run = RunStore(settings.runs_dir).load(queue.get(task.id).run_id or "")
    assert run.hands_off == ["TKT-1016"]
    refused = [o for o in run.observations if "escalated to a person by its sub-task" in o.output]
    assert refused, "the parent's attempt to change TKT-1016 must be refused"
    ticket = next(t for t in sandbox.rows("support_tickets") if t["id"] == "TKT-1016")
    assert ticket["status"] != "resolved"  # it stays with the person the sub-task handed it to
    assert queue.get(task.id).status == "completed"
