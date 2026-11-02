"""Схемы запросов и ответов API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ErrorBody(BaseModel):
    code: str = Field(examples=["document_not_found"])
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorOut(BaseModel):
    """Единый формат ошибки всех методов."""

    error: ErrorBody


# Настоящие правила проверяет сервис, ограничение здесь отсекает заведомо лишние данные.
Password = Annotated[str, Field(min_length=1, max_length=1024)]
OneTimeToken = Annotated[str, Field(min_length=1, max_length=256)]


class RegisterIn(BaseModel):
    email: EmailStr
    password: Password
    consent: bool = Field(description="Согласие на обработку адреса электронной почты")


class VerifyEmailIn(BaseModel):
    token: OneTimeToken


class LoginIn(BaseModel):
    email: EmailStr
    password: Password


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Срок действия в секундах")


class AcceptedOut(BaseModel):
    message: str


class UserOut(BaseModel):
    """Все персональные данные, которые хранит сервис."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    email_verified_at: datetime | None
    consent_at: datetime
    created_at: datetime


class PasswordChangeIn(BaseModel):
    current_password: Password
    new_password: Password


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: OneTimeToken
    new_password: Password


class DeleteAccountIn(BaseModel):
    password: Password


class ShareIn(BaseModel):
    email: EmailStr
    permission: Literal["read", "write"]


class ShareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str
    permission: str


class PreviewOut(BaseModel):
    html: str = Field(description="HTML документа: вставки HTML из текста экранированы")


class DocumentCreate(BaseModel):
    content: str = Field(description="Текст документа в Markdown, можно с YAML-шапкой")
    source_name: str | None = Field(default=None, description="Имя исходного файла")


class DocumentUpdate(BaseModel):
    content: str
    expected_version: int | None = Field(
        default=None,
        ge=1,
        description="Если указана и не совпадает с текущей, запрос завершится ошибкой 409",
    )


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version: int
    title: str
    tags: tuple[str, ...]
    category: str | None
    author: str | None
    created: date | None
    source_name: str | None
    created_at: datetime
    updated_at: datetime
    access: str = Field(description="Уровень доступа: owner, write или read")


class DocumentOut(DocumentSummary):
    content: str


class DocumentPage(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    title: str
    content_hash: str
    created_at: datetime


class VersionOut(VersionSummary):
    content: str


class DiffOut(BaseModel):
    base: int
    target: int
    diff: str


class SearchHitOut(BaseModel):
    id: str
    title: str
    score: float
    snippet: str = Field(description="HTML-фрагмент: текст экранирован, совпадения в <mark>")
    tags: list[str]
    category: str | None
    created: date | None


class SearchResponse(BaseModel):
    total: int
    scorer: str
    sort: str
    limit: int
    offset: int
    hits: list[SearchHitOut]


class FileFailureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    code: str
    message: str


class ImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    created: list[str]
    updated: list[str]
    unchanged: list[str]
    failed: list[FileFailureOut]


class NamedCountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    count: int


class StatsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    documents: int
    versions: int
    content_bytes: int
    indexed_documents: int
    indexed_terms: int
    cache_hits: int
    cache_misses: int
    top_tags: list[NamedCountOut]
    categories: list[NamedCountOut]


class HealthOut(BaseModel):
    status: str = Field(examples=["ok"], description="ok или degraded, если не отвечает кэш")
    database: str
    cache: str
