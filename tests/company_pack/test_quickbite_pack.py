"""The QuickBite pack loads, is consistent, and encodes the agreed policy."""

import pytest

from company_operator.company_pack import CompanyPack, load_pack
from company_operator.config import Settings
from sandbox.quickbite.seed import OPERATOR_PASSWORD, OPERATOR_USERNAME
from sandbox.quickbite.support import CATEGORIES


@pytest.fixture(scope="module")
def pack() -> CompanyPack:
    return load_pack(Settings().company_pack_dir)


def test_loads_everything(pack: CompanyPack) -> None:
    assert pack.company.name == "QuickBite"
    assert set(pack.systems) == {"support", "ops", "payments"}
    assert len(pack.sops) >= 10
    assert "tone" in pack.guides


def test_every_ticket_category_has_a_specific_sop(pack: CompanyPack) -> None:
    for category in CATEGORIES:
        if category == "other":
            continue
        specific = [s for s in pack.sops_for(category) if not s.general]
        assert specific, f"no SOP for {category}"


def test_general_sops_always_apply(pack: CompanyPack) -> None:
    ids = [s.id for s in pack.sops_for("late_delivery")]
    assert ids[0] == "late_delivery"
    assert {"every_ticket", "duplicate_ticket", "asking_the_customer"} <= set(ids)


def test_operator_credentials_match_the_sandbox(pack: CompanyPack) -> None:
    for system in pack.systems.values():
        assert system.credentials.username == OPERATOR_USERNAME
        assert system.credentials.password() == OPERATOR_PASSWORD


def test_credentials_prefer_environment(pack: CompanyPack, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUICKBITE_PAYMENTS_PASSWORD", "from-env")
    assert pack.systems["payments"].credentials.password() == "from-env"


def test_money_actions_are_tagged(pack: CompanyPack) -> None:
    assert pack.action("payments.refund").effect == "money"
    assert pack.action("payments.issue_coupon").effect == "money"
    assert pack.action("ops.cancel_order").effect == "irreversible"
    assert pack.action("support.read_ticket").effect == "read"


@pytest.mark.parametrize(
    ("minutes_late", "coupon"),
    [(0, 0), (12, 0), (15, 50), (29, 50), (35, 100), (47, 100), (60, 150), (72, 150), (240, 150)],
)
def test_late_delivery_tiers(pack: CompanyPack, minutes_late: int, coupon: int) -> None:
    assert pack.compensation.issues["late_delivery"].coupon_for_delay(minutes_late) == coupon


def test_item_remedies(pack: CompanyPack) -> None:
    issues = pack.compensation.issues
    assert issues["missing_item"].amount is not None and issues["missing_item"].amount.percent == 100
    assert issues["food_quality"].amount is not None and issues["food_quality"].amount.percent == 50
    assert issues["wrong_order"].amount is not None and issues["wrong_order"].amount.basis == "order_total"
    assert [f.target for f in issues["rider_behaviour"].follow_up] == ["rider"]


def triggered(pack: CompanyPack, action: str, **facts: object) -> set[str]:
    return {r.id for r in pack.approvals.triggered(action, facts)}  # type: ignore[arg-type]


def test_small_refund_needs_no_approval(pack: CompanyPack) -> None:
    assert (
        triggered(pack, "payments.refund", amount=60, reason="missing_item", customer_claims_30d=0) == set()
    )


def test_refund_over_500_needs_approval(pack: CompanyPack) -> None:
    assert triggered(pack, "payments.refund", amount=500.01) == {"high_value"}
    assert triggered(pack, "payments.refund", amount=500) == set()


def test_wrong_order_full_refund_needs_approval(pack: CompanyPack) -> None:
    assert triggered(pack, "payments.refund", amount=646, full_order_refund=True) == {
        "high_value",
        "full_order_refund",
    }


def test_repeat_claimant_needs_approval_even_for_small_amounts(pack: CompanyPack) -> None:
    assert triggered(pack, "payments.refund", amount=249, customer_claims_30d=4) == {"repeat_claimant"}


def test_duplicate_charge_reversal_needs_approval(pack: CompanyPack) -> None:
    assert triggered(pack, "payments.refund", amount=300, reason="duplicate_charge") == {"payment_reversal"}


def test_approval_rules_only_apply_to_their_actions(pack: CompanyPack) -> None:
    assert triggered(pack, "support.reply_to_customer", amount=10_000) == set()


def test_unsupplied_fact_never_triggers(pack: CompanyPack) -> None:
    assert triggered(pack, "payments.issue_coupon") == set()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hi, my Coke wasn't in the bag. Please refund it.", "missing_item"),
        ("Extremely late delivery. Order took 1.5 hours!!", "late_delivery"),
        ("I got someone else's order, two pizzas", "wrong_order"),
        ("The delivery guy was very rude", "rider_behaviour"),
        ("Restaurant cancelled but the money was debited", "payment_issue"),
        ("Please cancel my order, placed by mistake", "order_change"),
    ],
)
def test_keyword_search_surfaces_the_right_sop(pack: CompanyPack, text: str, expected: str) -> None:
    assert expected in [s.id for s in pack.search_sops(text)]
