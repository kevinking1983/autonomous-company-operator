import json
from pathlib import Path

from company_operator.audit.log import EventLog
from company_operator.runtime import (
    Criterion,
    CriterionResult,
    Plan,
    PlanStep,
    RunState,
    TaskContract,
    TaskRequest,
    ToolCall,
    Verification,
)
from company_operator.runtime.models import Observation
from company_operator.verify import write_report


def test_report_tells_the_whole_story(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.emit("llm.call", purpose="plan", model="gemini-test", input_tokens=1000, output_tokens=50)
    log.emit("adapt.decision", rule="reobserve_before_retry")
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "verify1-01-order-QB-48213.png").write_bytes(b"png")

    state = RunState(
        run_id="r1",
        request=TaskRequest(text="Resolve support ticket TKT-1001.", source="ticket", ticket_id="TKT-1001"),
    )
    state.phase = "completed"
    state.summary = "Refunded ₹60 for the missing Coke (RF-70012)."
    state.contract = TaskContract(
        outcome="Refund the Coke", success_criteria=[Criterion(id="c1", text="Refund of ₹60 exists")]
    )
    state.plan = Plan(
        steps=[PlanStep(id="refund", goal="Refund", expected="RF exists", status="done", outcome="RF-70012")]
    )
    state.observations = [
        Observation(seq=1, phase="execute", step_id="refund", ok=True,
                    output="URL: http://x/payments/transactions/PAY-1\nTitle: t\nNote: no payments.refund request was sent by this click; the authorisation was withdrawn.",
                    call=ToolCall(tool="browser_click", arguments={"ref": "e4", "action": "payments.refund", "facts": {"amount": 60}})),
        Observation(seq=2, phase="execute", step_id="refund", ok=True,
                    output="URL: http://x/payments/refunds/RF-70012?msg=Refund%20RF-70012%20of%20%E2%82%B960.00%20initiated.\nTitle: t",
                    call=ToolCall(tool="browser_click", arguments={"ref": "e5", "action": "payments.refund", "facts": {"amount": 60}})),
    ]  # fmt: skip
    state.verification = Verification(
        passed=True,
        results=[CriterionResult(criterion_id="c1", passed=True, evidence="RF-70012 ₹60.00 missing_item")],
    )

    report = write_report(state, tmp_path)
    assert json.loads((tmp_path / "report.json").read_text())["status"] == "completed"
    md = (tmp_path / "report.md").read_text()
    assert md.startswith("# Run r1: ✅ Completed and verified")
    assert "| `c1` | ✅ | RF-70012 ₹60.00 missing_item |" in md
    assert "| 2 | refund | `payments.refund` | amount=60 | ok Refund RF-70012 of ₹60.00 initiated. |" in md
    assert "| 1 |" not in md  # declared on the review button, nothing submitted: not a change
    assert "Model calls: 1 (gemini-test)" in md and "adaptations: reobserve_before_retry" in md
    assert "[verify1-01-order-QB-48213.png](evidence/verify1-01-order-QB-48213.png)" in md
    assert report["stats"]["input_tokens"] == 1000
