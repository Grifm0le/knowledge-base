"""Таблицы базы данных. Схема меняется только миграциями Alembic из каталога migrations."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Computed, Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column

SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('russian', title), 'A') || "
    "setweight(to_tsvector('russian', keywords), 'B') || "
    "setweight(to_tsvector('russian', body), 'C') || "
    "setweight(to_tsvector('russian', code), 'D')"
)


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    """Текущее состояние документа: метаданные и текст последней версии."""

    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_documents_tags", "tags", postgresql_using="gin"),
        Index("ix_documents_updated_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    source_name: Mapped[str | None] = mapped_column(Text, unique=True)
    current_version: Mapped[int] = mapped_column()
    title: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    category: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    created: Mapped[date | None] = mapped_column(Date)
    body: Mapped[str] = mapped_column(Text)
    keywords: Mapped[str] = mapped_column(Text, server_default="")
    code: Mapped[str] = mapped_column(Text, server_default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    search_vector: Mapped[str] = deferred(
        mapped_column(TSVECTOR, Computed(SEARCH_VECTOR_SQL, persisted=True))
    )


class DocumentVersionRow(Base):
    """Неизменяемая версия документа: исходный текст на момент сохранения."""

    __tablename__ = "document_versions"

    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
