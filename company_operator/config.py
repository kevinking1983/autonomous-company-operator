"""Runtime configuration, loaded from environment variables and an optional .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ACO_", extra="ignore")

    # Where runs, checkpoints, audit events and memory are stored.
    data_dir: Path = PROJECT_ROOT / "data"

    # Which Company Pack the operator works for.
    company_pack: str = "quickbite"
    company_packs_dir: Path = PROJECT_ROOT / "company_packs"

    # Operator API.
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Base URL of the sandboxed company systems.
    sandbox_url: str = "http://127.0.0.1:8100"

    # Browser automation.
    browser_headless: bool = True

    # LLM provider; chosen at the "agent brain" step.
    llm_provider: str = "unset"
    llm_model: str = ""
    llm_api_key: str = ""

    @property
    def company_pack_dir(self) -> Path:
        return self.company_packs_dir / self.company_pack


@lru_cache
def get_settings() -> Settings:
    return Settings()
