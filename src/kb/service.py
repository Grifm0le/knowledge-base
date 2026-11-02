"""Сервис базы знаний: связывает хранилище, поисковый индекс и кэш."""

from __future__ import annotations

import difflib
import logging
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import PurePath
from threading import RLock
from typing import Any

from kb.cache import SearchCache
from kb.errors import DocumentValidationError, DuplicateSourceError
from kb.models import ParsedDocument
from kb.normalize import decode_document
from kb.parser import parse_document
from kb.scoring import available_scorers
from kb.search import SearchEngine, SearchHit, SearchQuery, SearchResult
from kb.storage.repository import (
    DocumentDraft,
    DocumentRepository,
    DocumentVersion,
    StoredDocument,
)

logger = logging.getLogger(__name__)

STORAGE_SCORER = "pgfts"


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
class Stats:
    documents: int
    versions: int
    content_bytes: int
    indexed_documents: int
    indexed_terms: int
    cache_hits: int
    cache_misses: int


def search_methods() -> list[str]:
    return sorted([*available_scorers(), STORAGE_SCORER])


class KnowledgeBase:
    """Фасад для API. Сначала пишет в хранилище, затем обновляет индекс и сбрасывает кэш."""

    def __init__(
        self, repository: DocumentRepository, engine: SearchEngine, cache: SearchCache
    ) -> None:
        self._repository = repository
        self._engine = engine
        self._cache = cache
        # Индекс живёт в памяти процесса и не рассчитан на изменение во время чтения.
        self._index_lock = RLock()

    def load_index(self) -> int:
        """Строит индекс из хранилища при запуске сервиса."""
        started = time.perf_counter()
        count = 0
        with self._index_lock:
            for stored in self._repository.iter_current():
                self._engine.add_document(stored.id, _parse(stored.content, stored.source_name))
                count += 1
        # Кэш Redis переживает перезапуск сервиса, а данные за это время могли измениться.
        self._cache.invalidate()
        elapsed = time.perf_counter() - started
        logger.info("Индекс построен: документов %d за %.2f с", count, elapsed)
        return count

    def create_document(self, content: str, source_name: str | None = None) -> StoredDocument:
        draft = _draft(content, source_name)
        stored = self._repository.create(draft)
        self._reindex(stored.id, draft)
        logger.info("Документ %s создан: %r", stored.id, stored.title)
        return stored

    def update_document(
        self, doc_id: str, content: str, expected_version: int | None = None
    ) -> StoredDocument:
        draft = _draft(content, self._repository.get(doc_id).source_name)
        result = self._repository.update(doc_id, draft, expected_version)
        if result.changed:
            self._reindex(doc_id, draft)
            logger.info("Документ %s: новая версия %d", doc_id, result.document.version)
        return result.document

    def delete_document(self, doc_id: str) -> None:
        self._repository.delete(doc_id)
        with self._index_lock:
            self._engine.remove_document(doc_id)
        self._cache.invalidate()
        logger.info("Документ %s удалён", doc_id)

    def get_document(self, doc_id: str) -> StoredDocument:
        return self._repository.get(doc_id)

    def list_documents(self, limit: int, offset: int) -> tuple[list[StoredDocument], int]:
        return self._repository.list_documents(limit, offset)

    def list_versions(self, doc_id: str) -> list[DocumentVersion]:
        return self._repository.list_versions(doc_id)

    def get_version(self, doc_id: str, version: int) -> DocumentVersion:
        return self._repository.get_version(doc_id, version)

    def restore_version(self, doc_id: str, version: int) -> StoredDocument:
        """Возвращает старый текст новой версией: история при этом не теряется."""
        old = self._repository.get_version(doc_id, version)
        return self.update_document(doc_id, old.content)

    def diff_versions(self, doc_id: str, base: int, target: int) -> str:
        """Различия двух версий в формате unified diff."""
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
        key = self._cache.key_for(asdict(query))
        cached = self._cache.get(key)
        if cached is not None:
            return SearchOutcome(cached, cache_hit=True)
        payload = _payload(self._run_search(query), query)
        self._cache.put(key, payload)
        return SearchOutcome(payload, cache_hit=False)

    def stats(self) -> Stats:
        documents, versions, size = self._repository.counts()
        return Stats(
            documents=documents,
            versions=versions,
            content_bytes=size,
            indexed_documents=len(self._engine),
            indexed_terms=self._engine.index.term_count,
            cache_hits=self._cache.hits,
            cache_misses=self._cache.misses,
        )

    def check_storage(self) -> None:
        self._repository.ping()

    def check_cache(self) -> bool:
        return self._cache.ping()

    def _import_file(self, name: str, data: bytes, report: FileImportReport) -> None:
        if PurePath(name).suffix.lower() not in (".md", ".markdown"):
            raise DocumentValidationError("unsupported_file_type", "Ожидается файл Markdown (.md)")
        content = decode_document(data)
        existing = self._repository.find_by_source(name)
        if existing is None:
            self.create_document(content, source_name=name)
            report.created.append(name)
            return
        updated = self.update_document(existing.id, content)
        (report.updated if updated.version != existing.version else report.unchanged).append(name)

    def _run_search(self, query: SearchQuery) -> SearchResult:
        scores = None
        if query.scorer == STORAGE_SCORER and query.text.strip():
            scores = self._repository.fulltext_scores(query.text)
        with self._index_lock:
            if scores is not None:
                return self._engine.rank_scores(query, scores)
            if query.scorer == STORAGE_SCORER:
                return self._engine.rank_scores(query, dict.fromkeys(self._all_ids(), 0.0))
            return self._engine.search(query)

    def _all_ids(self) -> list[str]:
        return self._engine.index.doc_ids()

    def _reindex(self, doc_id: str, draft: DocumentDraft) -> None:
        with self._index_lock:
            self._engine.add_document(doc_id, draft.parsed)
        self._cache.invalidate()


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
