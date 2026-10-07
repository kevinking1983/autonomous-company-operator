import pytest

from company_operator.cli import main


def test_task_is_required() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_missing_api_key_is_explained(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ACO_LLM_API_KEY", "")
    monkeypatch.setenv("ACO_LLM_PROVIDER", "gemini")
    assert main(["--ticket", "TKT-1001"]) == 2
    assert "ACO_LLM_API_KEY" in capsys.readouterr().err


def test_inbox_and_decisions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: object
) -> None:
    from pathlib import Path

    from company_operator.audit.log import EventLog
    from company_operator.company_pack import load_pack
    from company_operator.config import Settings
    from company_operator.memory.store import OperatorDB
    from company_operator.policy import PolicyEngine
    from company_operator.tools.human import HumanChannel

    assert isinstance(tmp_path, Path)
    monkeypatch.setenv("ACO_DATA_DIR", str(tmp_path))
    settings = Settings()
    log = EventLog()
    channel = HumanChannel(
        PolicyEngine(load_pack(settings.company_pack_dir), log),
        log,
        OperatorDB(settings.db_path),
        run_id="r9",
    )
    decision = channel.policy.evaluate(
        "payments.refund",
        {"payment_id": "PAY-1", "amount": 249, "reason": "missing_item", "customer_claims_30d": 4},
    )
    channel.request_approval(decision, "5th missing-item claim this month")

    assert main(["--inbox"]) == 0
    assert "H-1001 [approval] run r9: 5th missing-item claim this month" in capsys.readouterr().out
    assert (
        main(["--reject", "H-1001", "--note", "No refunds for repeat claimants without proof.", "--remember"])
        == 0
    )
    out = capsys.readouterr().out
    assert "H-1001 rejected by priya.supervisor (remembered as a company fact)" in out
    assert "operator-run --resume r9" in out
    assert [f.text for f in OperatorDB(settings.db_path).facts()] == [
        "No refunds for repeat claimants without proof."
    ]
