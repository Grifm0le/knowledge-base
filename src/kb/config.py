"""Настройки сервиса из переменных окружения с префиксом KB_ или файла .env."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Отдельный класс: миграциям не нужны секреты приложения."""

    model_config = SettingsConfigDict(env_prefix="KB_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kb:kb@localhost:5432/kb"


class Settings(DatabaseSettings):
    # Без адреса Redis сервис работает, но результаты поиска не кэшируются.
    redis_url: str | None = None
    cache_ttl_seconds: int = Field(default=300, ge=1)
    cache_timeout_seconds: float = Field(default=0.25, gt=0)
    log_level: str = "INFO"
    sql_echo: bool = False

    # Значения по умолчанию нет: сервис не запустится с общеизвестным ключом подписи.
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_minutes: int = Field(default=30, ge=1)
    refresh_token_days: int = Field(default=14, ge=1)
    one_time_token_minutes: int = Field(default=30, ge=1)
    # Адрес для ссылок в письмах.
    public_url: str = "http://127.0.0.1:8000"
    # Запрещать отправку cookie без TLS. Отключается только при локальной разработке по HTTP.
    cookie_secure: bool = True
    docs_enabled: bool = True

    # Без адреса SMTP письма пишутся в журнал: это режим разработки.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "noreply@kb.example.com"
    smtp_starttls: bool = True
