"""Application configuration.

Secrets and environment-specific settings are read from environment variables
(optionally loaded from a local .env file). Nothing secret is ever hard-coded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (avoids an extra dependency)."""
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv()


def _db_path(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        relative = database_url[len("sqlite:///") :]
        if not os.path.isabs(relative):
            return str(PROJECT_ROOT / relative)
        return relative
    return database_url


@dataclass(frozen=True)
class Settings:
    ai_provider: str = os.getenv("AI_PROVIDER", "mock").strip().lower()
    ai_model: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    ai_api_key: str = os.getenv("AI_API_KEY", "")
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///lead_assistant.db")
    app_env: str = os.getenv("APP_ENV", "local")
    stale_days: int = int(os.getenv("STALE_DAYS", "7"))
    new_lead_window_hours: int = int(os.getenv("NEW_LEAD_WINDOW_HOURS", "48"))

    @property
    def db_path(self) -> str:
        return _db_path(self.database_url)


settings = Settings()
