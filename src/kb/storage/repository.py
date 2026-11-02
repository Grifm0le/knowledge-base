"""Репозиторий документов: интерфейс хранилища и реализация на PostgreSQL."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import Engine, Select, Text, cast, delete, func, literal_column, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.expression import ColumnClause

from kb.errors import (
    DocumentNotFoundError,
    DuplicateSourceError,
    VersionConflictError,
    VersionNotFoundError,
)
from kb.models import ParsedDocument
from kb.normalize import normalize_text
from kb.storage.tables import DocumentRow, DocumentVersionRow


def content_hash(content: str) -> str:
    """Хэш нормализованного текста: перевод строк CRLF не должен создавать новую версию."""
    return hashlib.sha256(normalize_text(content).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DocumentDraft:
    """Проверенный документ, готовый к сохранению."""

    content: str
    parsed: ParsedDocument
    source_name: str | None = None


@dataclass(frozen=True, slots=True)
class StoredDocument:
    id: str
    version: int
    title: str
    tags: tuple[str, ...]
    category: str | None
    author: str | None
    created: date | None
    content: str
    source_name: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    document_id: str
    version: int
    title: str
    content: str
    content_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SaveResult:
    document: StoredDocument
    changed: bool


class DocumentRepository(ABC):
    """Что сервису нужно от хранилища. Реализацию можно заменить, не меняя сервис."""

    @abstractmethod
    def create(self, draft: DocumentDraft) -> StoredDocument:
        """Создаёт документ с версией 1."""

    @abstractmethod
    def update(
        self, doc_id: str, draft: DocumentDraft, expected_version: int | None = None
    ) -> SaveResult:
        """Сохраняет новую версию. Если текст не изменился, версия не создаётся."""

    @abstractmethod
    def delete(self, doc_id: str) -> None:
        """Удаляет документ вместе с историей версий."""

    @abstractmethod
    def get(self, doc_id: str) -> StoredDocument:
        """Текущая версия документа."""

    @abstractmethod
    def find_by_source(self, source_name: str) -> StoredDocument | None:
        """Документ, импортированный из файла с таким именем."""

    @abstractmethod
    def list_documents(self, limit: int, offset: int) -> tuple[list[StoredDocument], int]:
        """Страница документов и их общее число, свежие первыми."""

    @abstractmethod
    def iter_current(self) -> Iterator[StoredDocument]:
        """Все документы: для построения индекса при запуске."""

    @abstractmethod
    def list_versions(self, doc_id: str) -> list[DocumentVersion]:
        """История версий, новые первыми."""

    @abstractmethod
    def get_version(self, doc_id: str, version: int) -> DocumentVersion:
        """Конкретная версия документа."""

    @abstractmethod
    def fulltext_scores(self, query_text: str) -> dict[str, float]:
        """Оценки документов по полнотекстовому поиску хранилища."""

    @abstractmethod
    def counts(self) -> tuple[int, int, int]:
        """Число документов, число версий и суммарный объём текстов в байтах."""

    @abstractmethod
    def ping(self) -> None:
        """Проверяет доступность хранилища. При недоступности выбрасывает исключение."""


class SqlDocumentRepository(DocumentRepository):
    """Хранилище на PostgreSQL. Каждый метод выполняется в своей транзакции."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def create(self, draft: DocumentDraft) -> StoredDocument:
        doc_id = str(uuid4())
        try:
            with self._sessions.begin() as session:
                row = DocumentRow(id=doc_id, source_name=draft.source_name, current_version=1)
                self._apply(row, draft)
                session.add(row)
                session.flush()
                self._add_version(session, doc_id, 1, draft)
                session.flush()
                session.refresh(row)
                return self._stored(row, draft.content)
        except IntegrityError as exc:
            raise DuplicateSourceError(f"Документ из файла {draft.source_name!r} уже есть") from exc

    def update(
        self, doc_id: str, draft: DocumentDraft, expected_version: int | None = None
    ) -> SaveResult:
        with self._sessions.begin() as session:
            # Блокировка строки: два одновременных сохранения получат разные номера версий.
            row = session.get(DocumentRow, doc_id, with_for_update=True)
            if row is None:
                raise DocumentNotFoundError(f"Документ не найден: {doc_id}")
            if expected_version is not None and expected_version != row.current_version:
                raise VersionConflictError(
                    f"Ожидалась версия {expected_version}, текущая {row.current_version}"
                )
            if row.content_hash == content_hash(draft.content):
                current = self._version_row(session, doc_id, row.current_version)
                return SaveResult(self._stored(row, current.content), changed=False)
            row.current_version += 1
            row.updated_at = func.now()
            self._apply(row, draft)
            self._add_version(session, doc_id, row.current_version, draft)
            session.flush()
            session.refresh(row)
            return SaveResult(self._stored(row, draft.content), changed=True)

    def delete(self, doc_id: str) -> None:
        with self._sessions.begin() as session:
            statement = delete(DocumentRow).where(DocumentRow.id == doc_id)
            if session.scalar(statement.returning(DocumentRow.id)) is None:
                raise DocumentNotFoundError(f"Документ не найден: {doc_id}")

    def get(self, doc_id: str) -> StoredDocument:
        with self._sessions() as session:
            row = session.get(DocumentRow, doc_id)
            if row is None:
                raise DocumentNotFoundError(f"Документ не найден: {doc_id}")
            current = self._version_row(session, doc_id, row.current_version)
            return self._stored(row, current.content)

    def find_by_source(self, source_name: str) -> StoredDocument | None:
        statement = self._current_select().where(DocumentRow.source_name == source_name)
        with self._sessions() as session:
            found = session.execute(statement).first()
            return self._stored(found[0], found[1]) if found else None

    def list_documents(self, limit: int, offset: int) -> tuple[list[StoredDocument], int]:
        statement = (
            self._current_select()
            .order_by(DocumentRow.updated_at.desc(), DocumentRow.id)
            .limit(limit)
            .offset(offset)
        )
        with self._sessions() as session:
            total = session.scalar(select(func.count()).select_from(DocumentRow)) or 0
            rows = session.execute(statement).all()
            return [self._stored(row, content) for row, content in rows], total

    def iter_current(self) -> Iterator[StoredDocument]:
        with self._sessions() as session:
            result = session.execute(self._current_select().execution_options(yield_per=500))
            for row, content in result:
                yield self._stored(row, content)

    def list_versions(self, doc_id: str) -> list[DocumentVersion]:
        statement = (
            select(DocumentVersionRow)
            .where(DocumentVersionRow.document_id == doc_id)
            .order_by(DocumentVersionRow.version.desc())
        )
        with self._sessions() as session:
            rows = session.scalars(statement).all()
            if not rows:
                raise DocumentNotFoundError(f"Документ не найден: {doc_id}")
            return [self._version(row) for row in rows]

    def get_version(self, doc_id: str, version: int) -> DocumentVersion:
        with self._sessions() as session:
            if session.get(DocumentRow, doc_id) is None:
                raise DocumentNotFoundError(f"Документ не найден: {doc_id}")
            return self._version(self._version_row(session, doc_id, version))

    def fulltext_scores(self, query_text: str) -> dict[str, float]:
        # websearch_to_tsquery требует все слова сразу, а остальные методы ищут любое из них.
        # Текст приводится к tsquery напрямую: to_tsquery заново обработал бы готовые основы слов.
        config: ColumnClause[str] = literal_column("'russian'::regconfig")
        parsed = func.websearch_to_tsquery(config, query_text.replace("\x00", " "))
        query = cast(func.replace(cast(parsed, Text), " & ", " | "), TSQUERY)
        rank = func.ts_rank_cd(DocumentRow.search_vector, query)
        statement = select(DocumentRow.id, rank).where(DocumentRow.search_vector.op("@@")(query))
        with self._sessions() as session:
            return {doc_id: float(score) for doc_id, score in session.execute(statement)}

    def counts(self) -> tuple[int, int, int]:
        statement = select(
            func.count(), func.coalesce(func.sum(func.octet_length(DocumentVersionRow.content)), 0)
        )
        with self._sessions() as session:
            documents = session.scalar(select(func.count()).select_from(DocumentRow)) or 0
            versions, size = session.execute(statement).one()
            return documents, versions, int(size)

    def ping(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    @staticmethod
    def _current_select() -> Select[tuple[DocumentRow, str]]:
        return select(DocumentRow, DocumentVersionRow.content).join(
            DocumentVersionRow,
            (DocumentVersionRow.document_id == DocumentRow.id)
            & (DocumentVersionRow.version == DocumentRow.current_version),
        )

    @staticmethod
    def _apply(row: DocumentRow, draft: DocumentDraft) -> None:
        meta = draft.parsed.metadata
        fields = draft.parsed.index_fields()
        row.title = meta.title
        row.tags = list(meta.tags)
        row.category = meta.category
        row.author = meta.author
        row.created = meta.created
        row.body = fields["body"]
        row.keywords = f"{fields['headings']} {fields['tags']}".strip()
        row.code = fields["code"]
        row.content_hash = content_hash(draft.content)

    @staticmethod
    def _add_version(session: Session, doc_id: str, version: int, draft: DocumentDraft) -> None:
        session.add(
            DocumentVersionRow(
                document_id=doc_id,
                version=version,
                title=draft.parsed.metadata.title,
                content=draft.content,
                content_hash=content_hash(draft.content),
            )
        )

    @staticmethod
    def _version_row(session: Session, doc_id: str, version: int) -> DocumentVersionRow:
        row = session.get(DocumentVersionRow, (doc_id, version))
        if row is None:
            raise VersionNotFoundError(f"У документа {doc_id} нет версии {version}")
        return row

    @staticmethod
    def _stored(row: DocumentRow, content: str) -> StoredDocument:
        return StoredDocument(
            id=row.id,
            version=row.current_version,
            title=row.title,
            tags=tuple(row.tags),
            category=row.category,
            author=row.author,
            created=row.created,
            content=content,
            source_name=row.source_name,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _version(row: DocumentVersionRow) -> DocumentVersion:
        return DocumentVersion(
            document_id=row.document_id,
            version=row.version,
            title=row.title,
            content=row.content,
            content_hash=row.content_hash,
            created_at=row.created_at,
        )
