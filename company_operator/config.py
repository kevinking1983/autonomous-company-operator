"""Runtime configuration, loaded from environment variables and an optional .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ACO_", extra="ignore")

    # Where runs, checkpoints, audit events and memory are stored.
    data_dir: Path = PROJECT_ROOT / "data"

    # Which Company Pack the operator works for.
    company_pack: str = "quickbite"
    company_packs_dir: Path = PROJECT_ROOT / "company_packs"

    # The person who approves requests from the command line.
    supervisor: str = "priya.supervisor"

    # Operator API.
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Base URL of the sandboxed company systems, and the key to its control API (evals and the
    # dashboard's fault switchboard use it; the operator never does).
    sandbox_url: str = "http://127.0.0.1:8100"
    sandbox_control_key: str = "sandbox-control"

    # Browser automation. Leave the executable unset to use Playwright's own Chromium.
    browser_headless: bool = True
    browser_executable: str | None = None

    # Language model. Provider: "gemini" or "openai_compatible" (Groq, OpenRouter, Ollama, ...).
    # Models are tried in order: when one is overloaded, the next takes over.
    llm_provider: str = "gemini"
    llm_models: str = "gemini-3-flash-preview,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    llm_api_key: str = ""
    llm_base_url: str = ""  # only for openai_compatible, e.g. https://api.groq.com/openai/v1
    llm_timeout: float = 90.0
    llm_min_interval: float = 2.0  # seconds between calls, to stay inside free-tier rate limits

    @field_validator("browser_executable", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `ACO_BROWSER_EXECUTABLE=` (as in .env.example) means "use Playwright's Chromium", not "launch ''".
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def model_list(self) -> list[str]:
        return [m.strip() for m in self.llm_models.split(",") if m.strip()]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "operator.db"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def evals_dir(self) -> Path:
        return self.data_dir / "evals"

    @property
    def company_pack_dir(self) -> Path:
        return self.company_packs_dir / self.company_pack


@lru_cache
def get_settings() -> Settings:
    return Settings()
