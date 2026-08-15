from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    app_secret: str = "development-only-change-this-secret"
    encryption_key: str = ""
    database_url: str = "postgresql+asyncpg://ppb:change-me@localhost:5432/ppb"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    telegram_api_id: int | None = None
    telegram_api_hash: str = ""
    telegram_bot_token: str = ""
    telegram_session_path: Path = Path("data/telegram/ppb")
    sentry_dsn: str = ""
    access_token_minutes: int = 30
    docs_enabled: bool = True
    worker_claim_idle_ms: int = Field(default=30000, ge=1000, le=300000)
    application_timezone: str = "Europe/London"
    demo_monitor_interval_seconds: int = Field(default=5, ge=1, le=8)

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def empty_telegram_api_id(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
