"""Сервис базы знаний: связывает хранилище, поисковые индексы пользователей и кэш."""

from __future__ import annotations

import difflib
import logging
import time
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from pathlib import PurePath
from threading import RLock
from typing import Any

from kb.cache import SearchCache
from kb.errors import (
    AccessDeniedError,
    DocumentNotFoundError,
    DocumentValidationError,
    DuplicateSourceError,
    InvalidQueryError,
    UserNotFoundError,
)
from kb.models import ParsedDocument
from kb.normalize import decode_document
from kb.parser import parse_document, render_html
from kb.ratelimit import Limit, RateLimiter
from kb.scoring import available_scorers
from kb.search import SearchEngine, SearchHit, SearchQuery, SearchResult
from kb.security import normalize_email
from kb.storage.repository import (
    ACCESS_OWNER,
    ACCESS_READ,
    ACCESS_WRITE,
    DocumentDraft,
    DocumentRepository,
    DocumentVersion,
    Share,
    StoredDocument,
)
from kb.storage.users import UserRepository

logger = logging.getLogger(__name__)

STORAGE_SCORER = "pgfts"
TOP_COUNTS = 10
# Подбор адресов через выдачу доступа: неудачная попытка не должна быть бесплатной.
SHARE_MISSES_BY_USER = Limit(30, 60 * 60)

_ACCESS_RANK = {ACCESS_READ: 1, ACCESS_WRITE: 2, ACCESS_OWNER: 3}


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    payload: dict[str, Any]
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class FileFailure:
    name: str
    code: str
    message: str


@dataclass(slots=True)
class FileImportReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[FileFailure] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NamedCount:
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class Stats:
    documents: int
    versions: int
    content_bytes: int
    indexed_documents: int
    indexed_terms: int
    cache_hits: int
    cache_misses: int
    top_tags: list[NamedCount]
    categories: list[NamedCount]


def search_methods() -> list[str]:
    return sorted([*available_scorers(), STORAGE_SCORER])


class SearchIndexes:
    """Индексы в памяти процесса, у каждого пользователя свой.

    Документ попадает в индекс владельца и всех, кому он открыт, поэтому чужой документ
    не может оказаться в выдаче. Ядро поиска при этом не изменилось.
    """

    def __init__(self) -> None:
        self._engines: dict[str, SearchEngine] = {}
        # Индекс не рассчитан на изменение во время чтения.
        self.lock = RLock()

    def engine(self, user_id: str) -> SearchEngine:
        with self.lock:
            engine = self._engines.get(user_id)
            if engine is None:
                engine = self._engines[user_id] = SearchEngine()
            return engine

    def put(self, doc_id: str, document: ParsedDocument, users: Iterable[str]) -> None:
        with self.lock:
            for user_id in users:
                self.engine(user_id).add_document(doc_id, document)

    def remove(self, doc_id: str, users: Iterable[str]) -> None:
        with self.lock:
            for user_id in users:
                # Сбой индекса уже не отменит удаление из базы, поэтому отсутствие записи не ошибка.
                with suppress(DocumentNotFoundError):
                    self.engine(user_id).remove_document(doc_id)

    def drop(self, user_id: str) -> None:
        with self.lock:
            self._engines.pop(user_id, None)


class KnowledgeBase:
    """Общая часть сервиса: индексы всех пользователей, запуск и проверка состояния."""

    def __init__(
        self,
        repository: DocumentRepository,
        users: UserRepository,
        cache: SearchCache,
        limiter: RateLimiter,
    ) -> None:
        self._repository = repository
        self._users = users
        self._cache = cache
        self._limiter = limiter
        self._indexes = SearchIndexes()

    def workspace(self, user_id: str) -> Workspace:
        """Операции от имени пользователя. Других способов обратиться к документам у API нет."""
        return Workspace(
            self._repository, self._users, self._indexes, self._cache, self._limiter, user_id
        )

    def load_index(self) -> int:
        """Строит индексы из хранилища при запуске сервиса."""
        started = time.perf_counter()
        shared: dict[str, list[str]] = {}
        for doc_id, user_id in self._repository.iter_shares():
            shared.setdefault(doc_id, []).append(user_id)
        count = 0
        for stored in self._repository.iter_current():
            parsed = _parse(stored.content, stored.source_name)
            self._indexes.put(stored.id, parsed, [stored.owner_id, *shared.get(stored.id, ())])
            count += 1
        # Кэш Redis переживает перезапуск сервиса, а данные за это время могли измениться.
        self._cache.invalidate_all()
        elapsed = time.perf_counter() - started
        logger.info("Индексы построены: документов %d за %.2f с", count, elapsed)
        return count

    def delete_user(self, user_id: str) -> None:
        """Удаляет учётную запись вместе с документами и убирает их из индексов других людей."""
        audiences = {
            doc_id: self._repository.audience(doc_id)
            for doc_id in self._repository.owned_document_ids(user_id)
        }
        self._users.delete(user_id)
        for doc_id, audience in audiences.items():
            others = [other for other in audience if other != user_id]
            self._indexes.remove(doc_id, others)
            for other in others:
                self._cache.invalidate(other)
        self._indexes.drop(user_id)
        self._cache.invalidate(user_id)
        logger.info("Учётная запись %s и документы (%d) удалены", user_id, len(audiences))

    def check_storage(self) -> None:
        self._repository.ping()

    def check_cache(self) -> bool:
        return self._cache.ping()


class Workspace:
    """Документы и поиск одного пользователя. Сначала пишет в хранилище, затем обновляет индексы.

    Нет доступа к документу: ответ такой же, как если бы документа не было, чтобы нельзя было
    узнать о существовании чужих записей. Доступ есть, но его уровня мало: отдельная ошибка.
    """

    def __init__(
        self,
        repository: DocumentRepository,
        users: UserRepository,
        indexes: SearchIndexes,
        cache: SearchCache,
        limiter: RateLimiter,
        user_id: str,
    ) -> None:
        self._repository = repository
        self._users = users
        self._indexes = indexes
        self._cache = cache
        self._limiter = limiter
        self.user_id = user_id

    def create_document(self, content: str, source_name: str | None = None) -> StoredDocument:
        draft = _draft(content, source_name)
        stored = self._repository.create(self.user_id, draft)
        self._reindex(stored.id, draft.parsed, [self.user_id])
        logger.info("Документ %s создан", stored.id)
        return stored

    def update_document(
        self, doc_id: str, content: str, expected_version: int | None = None
    ) -> StoredDocument:
        access = self._require(doc_id, ACCESS_WRITE)
        draft = _draft(content, self._repository.get(doc_id).source_name)
        result = self._repository.update(doc_id, draft, expected_version)
        if result.changed:
            self._reindex(doc_id, draft.parsed, self._repository.audience(doc_id))
            logger.info("Документ %s: новая версия %d", doc_id, result.document.version)
        return replace(result.document, access=access)

    def delete_document(self, doc_id: str) -> None:
        self._require(doc_id, ACCESS_OWNER)
        audience = self._repository.audience(doc_id)
        self._repository.delete(doc_id)
        self._indexes.remove(doc_id, audience)
        self._invalidate(audience)
        logger.info("Документ %s удалён", doc_id)

    def get_document(self, doc_id: str) -> StoredDocument:
        access = self._require(doc_id, ACCESS_READ)
        return replace(self._repository.get(doc_id), access=access)

    def list_documents(self, limit: int, offset: int) -> tuple[list[StoredDocument], int]:
        return self._repository.list_documents(self.user_id, limit, offset)

    def list_versions(self, doc_id: str) -> list[DocumentVersion]:
        self._require(doc_id, ACCESS_READ)
        return self._repository.list_versions(doc_id)

    def get_version(self, doc_id: str, version: int) -> DocumentVersion:
        self._require(doc_id, ACCESS_READ)
        return self._repository.get_version(doc_id, version)

    def restore_version(self, doc_id: str, version: int) -> StoredDocument:
        """Возвращает старый текст новой версией: история при этом не теряется."""
        self._require(doc_id, ACCESS_WRITE)
        old = self._repository.get_version(doc_id, version)
        return self.update_document(doc_id, old.content)

    def diff_versions(self, doc_id: str, base: int, target: int) -> str:
        """Различия двух версий в формате unified diff."""
        self._require(doc_id, ACCESS_READ)
        old = self._repository.get_version(doc_id, base)
        new = self._repository.get_version(doc_id, target)
        lines = difflib.unified_diff(
            old.content.splitlines(),
            new.content.splitlines(),
            fromfile=f"v{base}",
            tofile=f"v{target}",
            lineterm="",
        )
        return "\n".join(lines)

    def preview(self, doc_id: str) -> str:
        """Документ в виде HTML: вставки HTML из текста экранируются."""
        self._require(doc_id, ACCESS_READ)
        return render_html(self._repository.get(doc_id).content)

    def share(self, doc_id: str, email: str, permission: str) -> Share:
        """Открывает документ другому пользователю. Управлять доступом может только владелец."""
        self._require(doc_id, ACCESS_OWNER)
        self._limiter.check("share-miss", self.user_id, SHARE_MISSES_BY_USER)
        target = self._users.find_by_email(normalize_email(email))
        if target is None or target.email_verified_at is None:
            self._limiter.hit("share-miss", self.user_id, SHARE_MISSES_BY_USER)
            raise UserNotFoundError("Пользователь с таким адресом не найден")
        if target.id == self.user_id:
            raise InvalidQueryError("Владелец и так имеет полный доступ к документу")
        self._repository.set_share(doc_id, target.id, permission)
        stored = self._repository.get(doc_id)
        self._reindex(doc_id, _parse(stored.content, stored.source_name), [target.id])
        logger.info("Документ %s открыт пользователю %s: %s", doc_id, target.id, permission)
        return Share(target.id, target.email, permission)

    def unshare(self, doc_id: str, user_id: str) -> None:
        """Отзывает доступ. Пользователь может и сам отказаться от открытого ему документа."""
        self._require(doc_id, ACCESS_READ if user_id == self.user_id else ACCESS_OWNER)
        if not self._repository.remove_share(doc_id, user_id):
            raise UserNotFoundError("Этому пользователю доступ не выдан")
        self._indexes.remove(doc_id, [user_id])
        self._cache.invalidate(user_id)
        logger.info("Документ %s закрыт для пользователя %s", doc_id, user_id)

    def list_shares(self, doc_id: str) -> list[Share]:
        self._require(doc_id, ACCESS_OWNER)
        return self._repository.list_shares(doc_id)

    def import_files(self, files: Iterable[tuple[str, bytes]]) -> FileImportReport:
        """Импортирует файлы по одному. Плохой файл не мешает импорту остальных."""
        report = FileImportReport()
        for name, data in files:
            try:
                self._import_file(name, data, report)
            except (DocumentValidationError, DuplicateSourceError) as exc:
                logger.warning("Файл %s отклонён: %s", name, exc)
                report.failed.append(FileFailure(name, exc.code, str(exc)))
        logger.info(
            "Импорт: создано %d, обновлено %d, без изменений %d, отклонено %d",
            len(report.created),
            len(report.updated),
            len(report.unchanged),
            len(report.failed),
        )
        return report

    def search(self, query: SearchQuery) -> SearchOutcome:
        """Поиск с кэшированием выдачи: cache-aside с отменой по номеру поколения."""
        key = self._cache.key_for(self.user_id, asdict(query))
        cached = self._cache.get(key)
        if cached is not None:
            return SearchOutcome(cached, cache_hit=True)
        payload = _payload(self._run_search(query), query)
        self._cache.put(key, payload)
        return SearchOutcome(payload, cache_hit=False)

    def stats(self) -> Stats:
        documents, versions, size = self._repository.counts(self.user_id)
        engine = self._indexes.engine(self.user_id)
        with self._indexes.lock:
            tags = engine.tag_counts().most_common(TOP_COUNTS)
            categories = engine.category_counts().most_common(TOP_COUNTS)
            indexed_documents = len(engine)
            indexed_terms = engine.index.term_count
        return Stats(
            documents=documents,
            versions=versions,
            content_bytes=size,
            indexed_documents=indexed_documents,
            indexed_terms=indexed_terms,
            cache_hits=self._cache.hits,
            cache_misses=self._cache.misses,
            top_tags=[NamedCount(name, count) for name, count in tags],
            categories=[NamedCount(name, count) for name, count in categories],
        )

    def _require(self, doc_id: str, level: str) -> str:
        access = self._repository.access_level(doc_id, self.user_id)
        if access is None:
            raise DocumentNotFoundError(f"Документ {doc_id} не найден")
        if _ACCESS_RANK[access] < _ACCESS_RANK[level]:
            raise AccessDeniedError("Для этого действия недостаточно прав на документ")
        return access

    def _import_file(self, name: str, data: bytes, report: FileImportReport) -> None:
        if PurePath(name).suffix.lower() not in (".md", ".markdown"):
            raise DocumentValidationError("unsupported_file_type", "Ожидается файл Markdown (.md)")
        content = decode_document(data)
        existing = self._repository.find_by_source(self.user_id, name)
        if existing is None:
            self.create_document(content, source_name=name)
            report.created.append(name)
            return
        updated = self.update_document(existing.id, content)
        (report.updated if updated.version != existing.version else report.unchanged).append(name)

    def _run_search(self, query: SearchQuery) -> SearchResult:
        engine = self._indexes.engine(self.user_id)
        scores = None
        if query.scorer == STORAGE_SCORER and query.text.strip():
            scores = self._repository.fulltext_scores(
                self.user_id, query.text, dict(query.field_weights) or None
            )
        with self._indexes.lock:
            if scores is not None:
                return engine.rank_scores(query, scores)
            if query.scorer == STORAGE_SCORER:
                return engine.rank_scores(query, dict.fromkeys(engine.index.doc_ids(), 0.0))
            return engine.search(query)

    def _reindex(self, doc_id: str, document: ParsedDocument, users: Iterable[str]) -> None:
        audience = list(users)
        self._indexes.put(doc_id, document, audience)
        self._invalidate(audience)

    def _invalidate(self, users: Iterable[str]) -> None:
        for user_id in users:
            self._cache.invalidate(user_id)


def _parse(content: str, source_name: str | None) -> ParsedDocument:
    fallback = PurePath(source_name).stem if source_name else None
    return parse_document(content, fallback_title=fallback)


def _draft(content: str, source_name: str | None) -> DocumentDraft:
    decode_document(content.encode("utf-8"))  # проверка размера по тем же правилам, что у файлов
    return DocumentDraft(content, _parse(content, source_name), source_name)


def _payload(result: SearchResult, query: SearchQuery) -> dict[str, Any]:
    return {
        "total": result.total,
        "scorer": query.scorer,
        "sort": query.sort,
        "limit": query.limit,
        "offset": query.offset,
        "hits": [_hit_payload(hit) for hit in result.hits],
    }


def _hit_payload(hit: SearchHit) -> dict[str, Any]:
    return {
        "id": hit.doc_id,
        "title": hit.title,
        "score": round(hit.score, 6),
        "snippet": hit.snippet.to_html(),
        "tags": list(hit.tags),
        "category": hit.category,
        "created": hit.created.isoformat() if hit.created else None,
    }
