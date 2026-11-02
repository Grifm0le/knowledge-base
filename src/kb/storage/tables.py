"""Таблицы базы данных. Схема меняется только миграциями Alembic из каталога migrations."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
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


class UserRow(Base):
    """Учётная запись. Из персональных данных хранится только адрес электронной почты."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    # Входит в токены: увеличение номера отзывает все выданные токены пользователя.
    token_version: Mapped[int] = mapped_column(server_default="1")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthTokenRow(Base):
    """Разовый токен из письма. В базе лежит только его хэш."""

    __tablename__ = "auth_tokens"
    __table_args__ = (Index("ix_auth_tokens_user_id", "user_id"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    purpose: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentRow(Base):
    """Текущее состояние документа: метаданные и текст последней версии."""

    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_documents_tags", "tags", postgresql_using="gin"),
        Index("ix_documents_updated_at", "updated_at"),
        Index("ix_documents_owner_id", "owner_id"),
        UniqueConstraint("owner_id", "source_name", name="uq_documents_owner_source"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE", name="fk_documents_owner")
    )
    source_name: Mapped[str | None] = mapped_column(Text)
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


class DocumentShareRow(Base):
    """Доступ к документу другого пользователя."""

    __tablename__ = "document_shares"
    __table_args__ = (
        Index("ix_document_shares_user_id", "user_id"),
        CheckConstraint("permission IN ('read', 'write')", name="ck_document_shares_permission"),
    )

    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
