"""Репозиторий документов: интерфейс хранилища и реализация на PostgreSQL."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    REAL,
    ColumnElement,
    Engine,
    Select,
    Text,
    cast,
    delete,
    func,
    literal_column,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSQUERY, insert
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
from kb.storage.tables import DocumentRow, DocumentShareRow, DocumentVersionRow, UserRow

ACCESS_READ = "read"
ACCESS_WRITE = "write"
ACCESS_OWNER = "owner"


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
    owner_id: str
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
    # Уровень доступа пользователя, запросившего документ.
    access: str = ACCESS_OWNER


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


@dataclass(frozen=True, slots=True)
class Share:
    user_id: str
    email: str
    permission: str


class DocumentRepository(ABC):
    """Что сервису нужно от хранилища. Реализацию можно заменить, не меняя сервис."""

    @abstractmethod
    def create(self, owner_id: str, draft: DocumentDraft) -> StoredDocument:
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
    def find_by_source(self, owner_id: str, source_name: str) -> StoredDocument | None:
        """Документ пользователя, импортированный из файла с таким именем."""

    @abstractmethod
    def list_documents(
        self, user_id: str, limit: int, offset: int
    ) -> tuple[list[StoredDocument], int]:
        """Страница доступных пользователю документов и их общее число, свежие первыми."""

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
    def fulltext_scores(
        self, user_id: str, query_text: str, field_weights: Mapping[str, float] | None = None
    ) -> dict[str, float]:
        """Оценки доступных пользователю документов по полнотекстовому поиску хранилища."""

    @abstractmethod
    def counts(self, user_id: str) -> tuple[int, int, int]:
        """Число доступных документов, их версий и суммарный объём текстов в байтах."""

    @abstractmethod
    def access_level(self, doc_id: str, user_id: str) -> str | None:
        """Уровень доступа пользователя к документу или None, если доступа нет."""

    @abstractmethod
    def audience(self, doc_id: str) -> list[str]:
        """Все пользователи с доступом к документу: их индексы меняются вместе с ним."""

    @abstractmethod
    def set_share(self, doc_id: str, user_id: str, permission: str) -> None:
        """Выдаёт доступ к документу или меняет его уровень."""

    @abstractmethod
    def remove_share(self, doc_id: str, user_id: str) -> bool:
        """Отзывает доступ. False, если доступа не было."""

    @abstractmethod
    def list_shares(self, doc_id: str) -> list[Share]:
        """Пользователи, которым открыт документ."""

    @abstractmethod
    def iter_shares(self) -> Iterator[tuple[str, str]]:
        """Все пары документ и пользователь: для построения индексов при запуске."""

    @abstractmethod
    def owned_document_ids(self, owner_id: str) -> list[str]:
        """Документы пользователя: для очистки индексов при удалении учётной записи."""

    @abstractmethod
    def ping(self) -> None:
        """Проверяет доступность хранилища. При недоступности выбрасывает исключение."""


class SqlDocumentRepository(DocumentRepository):
    """Хранилище на PostgreSQL. Каждый метод выполняется в своей транзакции."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def create(self, owner_id: str, draft: DocumentDraft) -> StoredDocument:
        doc_id = str(uuid4())
        try:
            with self._sessions.begin() as session:
                row = DocumentRow(
                    id=doc_id, owner_id=owner_id, source_name=draft.source_name, current_version=1
                )
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

    def find_by_source(self, owner_id: str, source_name: str) -> StoredDocument | None:
        statement = self._current_select().where(
            DocumentRow.owner_id == owner_id, DocumentRow.source_name == source_name
        )
        with self._sessions() as session:
            found = session.execute(statement).first()
            return self._stored(found[0], found[1]) if found else None

    def list_documents(
        self, user_id: str, limit: int, offset: int
    ) -> tuple[list[StoredDocument], int]:
        visible = self._visible_to(user_id)
        statement = (
            self._current_select()
            .add_columns(DocumentShareRow.permission)
            .outerjoin(
                DocumentShareRow,
                (DocumentShareRow.document_id == DocumentRow.id)
                & (DocumentShareRow.user_id == user_id),
            )
            .where(visible)
            .order_by(DocumentRow.updated_at.desc(), DocumentRow.id)
            .limit(limit)
            .offset(offset)
        )
        counted = select(func.count()).select_from(DocumentRow).where(visible)
        with self._sessions() as session:
            total = session.scalar(counted) or 0
            rows = session.execute(statement).all()
            return [
                self._stored(row, content, ACCESS_OWNER if row.owner_id == user_id else permission)
                for row, content, permission in rows
            ], total

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

    def fulltext_scores(
        self, user_id: str, query_text: str, field_weights: Mapping[str, float] | None = None
    ) -> dict[str, float]:
        # websearch_to_tsquery требует все слова сразу, а остальные методы ищут любое из них.
        # Текст приводится к tsquery напрямую: to_tsquery заново обработал бы готовые основы слов.
        config: ColumnClause[str] = literal_column("'russian'::regconfig")
        parsed = func.websearch_to_tsquery(config, query_text.replace("\x00", " "))
        query = cast(func.replace(cast(parsed, Text), " & ", " | "), TSQUERY)
        if field_weights:
            weights = cast(_rank_weights(field_weights), ARRAY(REAL))
            rank = func.ts_rank_cd(weights, DocumentRow.search_vector, query)
        else:
            rank = func.ts_rank_cd(DocumentRow.search_vector, query)
        statement = select(DocumentRow.id, rank).where(
            DocumentRow.search_vector.op("@@")(query), self._visible_to(user_id)
        )
        with self._sessions() as session:
            scores = {doc_id: float(score) for doc_id, score in session.execute(statement)}
        # Поле с нулевым весом не участвует в поиске: совпадение только в нём не считается.
        return {doc_id: score for doc_id, score in scores.items() if score > 0 or not field_weights}

    def counts(self, user_id: str) -> tuple[int, int, int]:
        visible = self._visible_to(user_id)
        documents = select(func.count()).select_from(DocumentRow).where(visible)
        versions = (
            select(
                func.count(),
                func.coalesce(func.sum(func.octet_length(DocumentVersionRow.content)), 0),
            )
            .join(DocumentRow, DocumentRow.id == DocumentVersionRow.document_id)
            .where(visible)
        )
        with self._sessions() as session:
            document_count = session.scalar(documents) or 0
            version_count, size = session.execute(versions).one()
            return document_count, version_count, int(size)

    def access_level(self, doc_id: str, user_id: str) -> str | None:
        with self._sessions() as session:
            owner_id = session.scalar(select(DocumentRow.owner_id).where(DocumentRow.id == doc_id))
            if owner_id is None:
                return None
            if owner_id == user_id:
                return ACCESS_OWNER
            return session.scalar(
                select(DocumentShareRow.permission).where(
                    DocumentShareRow.document_id == doc_id, DocumentShareRow.user_id == user_id
                )
            )

    def audience(self, doc_id: str) -> list[str]:
        with self._sessions() as session:
            owner_id = session.scalar(select(DocumentRow.owner_id).where(DocumentRow.id == doc_id))
            if owner_id is None:
                return []
            shared = session.scalars(
                select(DocumentShareRow.user_id).where(DocumentShareRow.document_id == doc_id)
            )
            return [owner_id, *shared]

    def set_share(self, doc_id: str, user_id: str, permission: str) -> None:
        statement = insert(DocumentShareRow).values(
            document_id=doc_id, user_id=user_id, permission=permission
        )
        with self._sessions.begin() as session:
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=["document_id", "user_id"], set_={"permission": permission}
                )
            )

    def remove_share(self, doc_id: str, user_id: str) -> bool:
        statement = delete(DocumentShareRow).where(
            DocumentShareRow.document_id == doc_id, DocumentShareRow.user_id == user_id
        )
        with self._sessions.begin() as session:
            return session.scalar(statement.returning(DocumentShareRow.user_id)) is not None

    def list_shares(self, doc_id: str) -> list[Share]:
        statement = (
            select(DocumentShareRow.user_id, UserRow.email, DocumentShareRow.permission)
            .join(UserRow, UserRow.id == DocumentShareRow.user_id)
            .where(DocumentShareRow.document_id == doc_id)
            .order_by(UserRow.email)
        )
        with self._sessions() as session:
            return [Share(*row) for row in session.execute(statement)]

    def iter_shares(self) -> Iterator[tuple[str, str]]:
        statement = select(DocumentShareRow.document_id, DocumentShareRow.user_id)
        with self._sessions() as session:
            yield from (tuple(row) for row in session.execute(statement))

    def owned_document_ids(self, owner_id: str) -> list[str]:
        with self._sessions() as session:
            return list(
                session.scalars(select(DocumentRow.id).where(DocumentRow.owner_id == owner_id))
            )

    def ping(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    @staticmethod
    def _visible_to(user_id: str) -> ColumnElement[bool]:
        shared = select(DocumentShareRow.document_id).where(DocumentShareRow.user_id == user_id)
        return or_(DocumentRow.owner_id == user_id, DocumentRow.id.in_(shared))

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
    def _stored(row: DocumentRow, content: str, access: str = ACCESS_OWNER) -> StoredDocument:
        return StoredDocument(
            id=row.id,
            owner_id=row.owner_id,
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
            access=access,
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


def _rank_weights(field_weights: Mapping[str, float]) -> list[float]:
    """Веса полей в порядке D, C, B, A для ts_rank_cd. Функция принимает значения от 0 до 1."""
    code = field_weights.get("code", 0.0)
    body = field_weights.get("body", 0.0)
    keywords = max(field_weights.get("headings", 0.0), field_weights.get("tags", 0.0))
    title = field_weights.get("title", 0.0)
    largest = max(code, body, keywords, title) or 1.0
    return [value / largest for value in (code, body, keywords, title)]
