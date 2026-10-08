import pytest

from company_operator.config import Settings


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_browser_executable_uses_playwright_chromium(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ACO_BROWSER_EXECUTABLE", value)
    assert Settings(_env_file=None).browser_executable is None  # type: ignore[call-arg]


def test_browser_executable_path_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACO_BROWSER_EXECUTABLE", "/opt/chromium")
    assert Settings(_env_file=None).browser_executable == "/opt/chromium"  # type: ignore[call-arg]
