"""Схемы запросов и ответов API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorBody(BaseModel):
    code: str = Field(examples=["document_not_found"])
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorOut(BaseModel):
    """Единый формат ошибки всех методов."""

    error: ErrorBody


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


class StatsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    documents: int
    versions: int
    content_bytes: int
    indexed_documents: int
    indexed_terms: int
    cache_hits: int
    cache_misses: int


class HealthOut(BaseModel):
    status: str = Field(examples=["ok"], description="ok или degraded, если не отвечает кэш")
    database: str
    cache: str
