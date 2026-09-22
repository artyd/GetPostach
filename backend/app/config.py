"""Application configuration, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "sqlite:///./pigulkin.db"
    cphi_data_dir: str = "C:/Projects/Артем/CPHI_MILAN/data"
    cphi_repo_dir: str = "C:/Projects/Артем/CPHI_MILAN"

    anthropic_api_key: str = ""
    chat_model: str = "claude-sonnet-4-6"
    chat_max_tokens: int = 4096
    # Cheap model for conversation compaction (summaries) and bulk batch letters.
    summary_model: str = "claude-haiku-4-5"
    batch_model: str = "claude-sonnet-4-6"

    cors_origins: str = "http://localhost:8765,https://artyd.github.io"
    app_env: str = "dev"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def chat_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
