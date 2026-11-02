"""Настройки сервиса из переменных окружения с префиксом KB_ или файла .env."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KB_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kb:kb@localhost:5432/kb"
    # Без адреса Redis сервис работает, но результаты поиска не кэшируются.
    redis_url: str | None = None
    cache_ttl_seconds: int = Field(default=300, ge=1)
    cache_timeout_seconds: float = Field(default=0.25, gt=0)
    log_level: str = "INFO"
    sql_echo: bool = False
