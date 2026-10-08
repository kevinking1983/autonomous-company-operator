"""The independent verifier: evidence gathering, judging, integrity checks."""

from pathlib import Path

import pytest

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.llm import FunctionCall, LLMRequest, LLMResponse
from company_operator.runtime import Criterion, RunState, TaskContract, TaskRequest
from company_operator.tools import ToolContext
from company_operator.verify import IndependentVerifier
from company_operator.verify.verifier import find_records, integrity_checks, parse_verdicts
from tests.conftest import LiveSandbox


class FakeAuditor:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self.results = results
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            text="", calls=[FunctionCall("submit_verdicts", {"results": self.results})], model="fake-auditor"
        )


def missing_item_state() -> RunState:
    state = RunState(
        run_id="r1", request=TaskRequest(text="Resolve support ticket TKT-1001.", ticket_id="TKT-1001")
    )
    state.contract = TaskContract(
        outcome="Refund the missing Coke on QB-48213",
        success_criteria=[
            Criterion(id="c1", text="A refund of ₹60.00 with reason missing_item exists for order QB-48213."),
            Criterion(id="c2", text="Ticket TKT-1001 is resolved with category missing_item."),
        ],
    )
    state.memory = {"payment_id": "PAY-90031"}
    return state


def test_find_records_puts_criteria_first(pack: CompanyPack) -> None:
    assert find_records(pack, missing_item_state()) == [
        ("order", "QB-48213"),
        ("ticket", "TKT-1001"),
        ("payment", "PAY-90031"),
    ]


async def test_gathers_evidence_and_judges_without_the_operators_reasoning(
    ctx: ToolContext, sandbox: LiveSandbox, pack: CompanyPack
) -> None:
    auditor = FakeAuditor([
        {"criterion_id": "c1", "passed": False, "evidence": "Refund ledger for QB-48213: no refunds."},
        {"criterion_id": "c2", "passed": False, "evidence": "Ticket status open, category untriaged."},
    ])  # fmt: skip
    state = missing_item_state()
    state.hints = ["OPERATOR-ONLY HINT"]
    verification = await IndependentVerifier(auditor).verify(state, ctx)

    assert not verification.passed
    assert [r.criterion_id for r in verification.results] == ["c1", "c2", "integrity.money_once"]
    prompt = auditor.requests[0].prompt
    assert "Coke 500ml" in prompt and "NOT PACKED" in prompt  # the order page
    assert "TKT-1001: Item missing from my order" in prompt  # the ticket page
    assert "OPERATOR-ONLY HINT" not in prompt and "OPERATOR-ONLY" not in auditor.requests[0].system
    assert [t.name for t in auditor.requests[0].tools] == ["submit_verdicts"]
    shots = sorted(p.name for p in ctx.evidence_dir.glob("verify*.png"))
    assert shots[0] == "verify0-01-order-QB-48213.png" and len(shots) >= 5


async def test_verifier_session_cannot_change_anything(ctx: ToolContext, sandbox: LiveSandbox) -> None:
    await IndependentVerifier(FakeAuditor([])).verify(missing_item_state(), ctx)
    assert not ctx.log.of_type("policy.grant")  # it never authorises anything
    assert not [r for r in sandbox.rows("pay_refunds") if r["order_id"] == "QB-48213"]


def test_skipped_criterion_fails() -> None:
    results = parse_verdicts(
        missing_item_state(),
        {"results": [{"criterion_id": "c1: refund", "passed": True, "evidence": "RF-70012 ₹60.00"}]},
    )
    assert [(r.criterion_id, r.passed) for r in results] == [("c1", True), ("c2", False)]
    assert results[1].evidence == "The auditor gave no verdict."


def _log_with_refunds(*payments: str) -> EventLog:
    log = EventLog()
    for i, payment in enumerate(payments, 1):
        log.emit(
            "policy.grant",
            grant=f"G{i}",
            action="payments.refund",
            facts={"payment_id": payment, "amount": 60, "reason": "missing_item", "customer_claims_30d": i},
        )
        log.emit(
            "guard.allowed",
            grant=f"G{i}",
            method="POST",
            path=f"/payments/transactions/{payment}/refund/confirm",
        )
    return log


def test_integrity_passes_for_distinct_money_movements() -> None:
    [check] = integrity_checks(_log_with_refunds("PAY-1", "PAY-2"))
    assert check.passed and "PAY-1" in check.evidence and "PAY-2" in check.evidence


def test_integrity_catches_the_same_refund_twice() -> None:
    [check] = integrity_checks(_log_with_refunds("PAY-1", "PAY-1"))
    assert not check.passed and "performed 2 times" in check.evidence


def _uncertain_then_retry(*, reread: bool) -> EventLog:
    log = EventLog()
    path = "/payments/transactions/PAY-1/refund/confirm"
    for i in (1, 2):
        log.emit(
            "policy.grant",
            grant=f"G{i}",
            action="payments.refund",
            facts={"payment_id": "PAY-1", "amount": 60},
        )
        log.emit("guard.allowed", grant=f"G{i}", method="POST", path=path)
        if i == 1:
            log.emit("browser.uncertain", method="POST", url=f"http://x{path}", status=500)
            if reread:
                log.emit("tool.call", tool="browser_open", arguments={"path": "transactions/PAY-1"})
    return log


def test_retry_after_an_uncertain_attempt_needs_a_look_first() -> None:
    # Regression (fault suite): a 500 before anything happened, the operator re-checked, saw no refund and
    # tried again. One refund, but the check counted two submissions and the correct run escalated.
    [checked] = integrity_checks(_uncertain_then_retry(reread=True))
    assert checked.passed and "re-read the records" in checked.evidence
    # Retrying blindly is exactly how money moves twice (the first attempt may have committed).
    [blind] = integrity_checks(_uncertain_then_retry(reread=False))
    assert not blind.passed and "without checking first" in blind.evidence


def test_refused_submissions_are_not_money_moved() -> None:
    log = _log_with_refunds("PAY-1", "PAY-1")
    events = log.events
    log = EventLog()
    for e in events[:2]:
        log.emit(e.type, **e.data)
    log.emit("policy.grant_released", grant="G0")
    for e in events[2:]:
        log.emit(e.type, **e.data)
    [check] = integrity_checks(log)
    assert check.passed


def test_money_twice_across_a_pause_and_resume_is_caught(tmp_path: Path) -> None:
    # Regression: a run that resumed in another process started an empty log, so a refund made before the
    # pause and repeated after it looked like one. Grant ids also restart ("G1" in both processes).
    path = tmp_path / "events.jsonl"
    for _ in range(2):  # before the pause, then after resuming in a "new process"
        log = EventLog(path)
        log.emit(
            "policy.grant", grant="G1", action="payments.refund", facts={"payment_id": "PAY-1", "amount": 60}
        )
        log.emit(
            "guard.allowed", grant="G1", method="POST", path="/payments/transactions/PAY-1/refund/confirm"
        )
    assert [e.seq for e in log.events] == [1, 2, 3, 4]  # numbering carries on
    [check] = integrity_checks(log)
    assert not check.passed and "performed 2 times" in check.evidence


@pytest.mark.parametrize("text", ["refund on qb-48213", "QB-48213, again QB-48213"])
def test_record_ids_are_normalised_and_deduplicated(pack: CompanyPack, text: str) -> None:
    assert pack.records["order"].find(text) == ["QB-48213"]
