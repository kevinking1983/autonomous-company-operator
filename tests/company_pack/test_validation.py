"""A broken pack fails loudly, listing every problem."""

import shutil
from pathlib import Path

import pytest

from company_operator.company_pack import PackError, load_pack
from company_operator.config import Settings


@pytest.fixture
def pack_dir(tmp_path: Path) -> Path:
    target = tmp_path / "quickbite"
    shutil.copytree(Settings().company_pack_dir, target)
    return target


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not in {path.name}"
    path.write_text(text.replace(old, new, 1))


def test_copy_is_valid(pack_dir: Path) -> None:
    load_pack(pack_dir)


def test_missing_directory() -> None:
    with pytest.raises(PackError, match="does not exist"):
        load_pack(Path("/nonexistent/pack"))


def test_sop_with_unknown_action(pack_dir: Path) -> None:
    edit(pack_dir / "sops" / "missing_item.md", "payments.refund,", "payments.wire_transfer,")
    with pytest.raises(PackError, match=r"unknown action 'payments\.wire_transfer'"):
        load_pack(pack_dir)


def test_approval_rule_with_unknown_fact(pack_dir: Path) -> None:
    edit(pack_dir / "policies" / "approvals.yaml", "fact: amount", "fact: amount_in_dollars")
    with pytest.raises(PackError, match="unknown fact 'amount_in_dollars'"):
        load_pack(pack_dir)


def test_gap_in_coupon_tiers(pack_dir: Path) -> None:
    edit(pack_dir / "policies" / "compensation.yaml", "{min_minutes_late: 30,", "{min_minutes_late: 31,")
    with pytest.raises(PackError, match="contiguous"):
        load_pack(pack_dir)


def test_action_in_unknown_system(pack_dir: Path) -> None:
    edit(
        pack_dir / "permissions.yaml",
        "system: ops\n    effect: irreversible",
        "system: crm\n    effect: irreversible",
    )
    with pytest.raises(PackError, match="unknown system 'crm'"):
        load_pack(pack_dir)


def test_multiple_problems_reported_together(pack_dir: Path) -> None:
    edit(pack_dir / "sops" / "late_delivery.md", "systems: [ops,", "systems: [crm,")
    edit(pack_dir / "policies" / "approvals.yaml", "fact: batch_size", "fact: mood")
    with pytest.raises(PackError) as err:
        load_pack(pack_dir)
    assert len(err.value.problems) == 2


def test_unknown_field_is_rejected(pack_dir: Path) -> None:
    edit(pack_dir / "company.yaml", "currency: INR", "currency: INR\nmotto: yum")
    with pytest.raises(PackError, match="motto"):
        load_pack(pack_dir)


def test_side_effect_without_request_pattern(pack_dir: Path) -> None:
    edit(pack_dir / "permissions.yaml", '    requests:\n      - {method: POST, path: "/ops/incidents"}\n', "")
    with pytest.raises(
        PackError, match=r"ops\.raise_incident: a write action needs at least one request pattern"
    ):
        load_pack(pack_dir)
