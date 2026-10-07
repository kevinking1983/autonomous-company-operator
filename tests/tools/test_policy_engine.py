"""Policy decisions and request enforcement, without a browser."""

import pytest

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.policy import PolicyEngine


@pytest.fixture
def engine(pack: CompanyPack) -> PolicyEngine:
    return PolicyEngine(pack, EventLog())


REFUND = "/payments/transactions/PAY-1/refund/confirm"


def test_unknown_action_denied(engine: PolicyEngine) -> None:
    assert engine.evaluate("payments.wire_transfer", {}).outcome == "deny"


def test_money_action_must_name_its_target(engine: PolicyEngine) -> None:
    decision = engine.evaluate("payments.refund", {"amount": 60})
    assert decision.outcome == "deny"
    assert "payment_id" in decision.reasons[0] and "reason" in decision.reasons[0]


def test_reads_and_sign_in_need_no_grant(engine: PolicyEngine) -> None:
    assert engine.guard("GET", "/payments/refunds", {}).allowed
    assert engine.guard("POST", "/payments/login", {"username": "x"}).allowed
    assert engine.guard(
        "POST", "/payments/transactions/PAY-1/refund", {"amount": "60"}
    ).allowed  # review page


def test_unknown_post_blocked(engine: PolicyEngine) -> None:
    verdict = engine.guard("POST", "/payments/admin/wipe", {})
    assert not verdict.allowed and "does not correspond" in verdict.reason


def test_grant_binds_path_and_form(engine: PolicyEngine) -> None:
    _, grant = engine.authorize(
        "payments.refund", {"payment_id": "PAY-1", "amount": 60, "reason": "missing_item"}
    )
    assert grant is not None
    assert not engine.guard(
        "POST",
        "/payments/transactions/PAY-2/refund/confirm",
        {"amount": "6000", "reason_code": "missing_item"},
    ).allowed
    assert not engine.guard("POST", REFUND, {"amount": "6001", "reason_code": "missing_item"}).allowed
    assert not engine.guard("POST", REFUND, {"amount": "6000", "reason_code": "goodwill"}).allowed
    assert engine.guard("POST", REFUND, {"amount": "6000", "reason_code": "missing_item"}).allowed
    assert not engine.guard(
        "POST", REFUND, {"amount": "6000", "reason_code": "missing_item"}
    ).allowed  # used up


def test_release_restores_a_use(engine: PolicyEngine) -> None:
    _, grant = engine.authorize(
        "payments.refund", {"payment_id": "PAY-1", "amount": 60, "reason": "missing_item"}
    )
    assert grant is not None
    form = {"amount": "6000", "reason_code": "missing_item"}
    verdict = engine.guard("POST", REFUND, form)
    engine.release(str(verdict.grant_id))
    assert engine.guard("POST", REFUND, form).allowed


def test_write_grants_are_reusable_until_expiry(engine: PolicyEngine) -> None:
    engine.authorize("support.add_internal_note", {"ticket_id": "TKT-1"})
    for _ in range(3):
        assert engine.guard("POST", "/support/tickets/TKT-1/note", {"body": "x"}).allowed
    assert not engine.guard("POST", "/support/tickets/TKT-2/note", {"body": "x"}).allowed


def test_approval_turns_into_grant(engine: PolicyEngine) -> None:
    facts = {"payment_id": "PAY-1", "amount": 900, "reason": "wrong_order"}
    decision, grant = engine.authorize("payments.refund", facts)
    assert decision.outcome == "needs_approval" and grant is None
    with pytest.raises(ValueError, match="Only needs_approval"):
        engine.grant_approved(engine.evaluate("payments.read", {}), "H0", "x")
    approved = engine.grant_approved(decision, "H1", "priya.supervisor")
    assert engine.find_grant("payments.refund", facts) is approved
    assert engine.guard("POST", REFUND, {"amount": "90000", "reason_code": "wrong_order"}).allowed


def test_every_decision_is_logged(pack: CompanyPack) -> None:
    log = EventLog()
    engine = PolicyEngine(pack, log)
    engine.evaluate("payments.refund", {"payment_id": "P", "amount": 1, "reason": "x"})
    engine.guard("POST", "/payments/admin/wipe", {})
    assert [e.type for e in log.events] == ["policy.decision", "guard.blocked"]
