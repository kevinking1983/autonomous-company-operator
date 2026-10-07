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
