"""Тестовые заменители внешних зависимостей."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from uuid import uuid4

import redis

from kb.errors import (
    DocumentNotFoundError,
    DuplicateSourceError,
    VersionConflictError,
    VersionNotFoundError,
)
from kb.storage.repository import (
    DocumentDraft,
    DocumentRepository,
    DocumentVersion,
    SaveResult,
    StoredDocument,
    content_hash,
)


class BrokenRedis:
    """Клиент Redis, у которого пропало соединение."""

    def _fail(self, *args: object, **kwargs: object) -> None:
        raise redis.ConnectionError("соединение разорвано")

    get = set = incr = ping = _fail


class InMemoryRepository(DocumentRepository):
    """Хранилище в памяти: проверяет логику сервиса без базы данных."""

    def __init__(self) -> None:
        self._documents: dict[str, StoredDocument] = {}
        self._versions: dict[str, list[DocumentVersion]] = {}

    def create(self, draft: DocumentDraft) -> StoredDocument:
        if draft.source_name and self.find_by_source(draft.source_name):
            raise DuplicateSourceError(draft.source_name)
        now = datetime.now(timezone.utc)
        stored = self._stored(str(uuid4()), 1, draft, now, now)
        self._documents[stored.id] = stored
        self._versions[stored.id] = [self._version(stored, draft, now)]
        return stored

    def update(
        self, doc_id: str, draft: DocumentDraft, expected_version: int | None = None
    ) -> SaveResult:
        current = self.get(doc_id)
        if expected_version is not None and expected_version != current.version:
            raise VersionConflictError("конфликт версий")
        if content_hash(current.content) == content_hash(draft.content):
            return SaveResult(current, changed=False)
        now = datetime.now(timezone.utc)
        stored = self._stored(doc_id, current.version + 1, draft, current.created_at, now)
        self._documents[doc_id] = stored
        self._versions[doc_id].append(self._version(stored, draft, now))
        return SaveResult(stored, changed=True)

    def delete(self, doc_id: str) -> None:
        self.get(doc_id)
        del self._documents[doc_id]
        del self._versions[doc_id]

    def get(self, doc_id: str) -> StoredDocument:
        try:
            return self._documents[doc_id]
        except KeyError:
            raise DocumentNotFoundError(doc_id) from None

    def find_by_source(self, source_name: str) -> StoredDocument | None:
        return next((d for d in self._documents.values() if d.source_name == source_name), None)

    def list_documents(self, limit: int, offset: int) -> tuple[list[StoredDocument], int]:
        items = sorted(self._documents.values(), key=lambda d: d.updated_at, reverse=True)
        return items[offset : offset + limit], len(items)

    def iter_current(self) -> Iterator[StoredDocument]:
        return iter(list(self._documents.values()))

    def list_versions(self, doc_id: str) -> list[DocumentVersion]:
        self.get(doc_id)
        return list(reversed(self._versions[doc_id]))

    def get_version(self, doc_id: str, version: int) -> DocumentVersion:
        self.get(doc_id)
        for item in self._versions[doc_id]:
            if item.version == version:
                return item
        raise VersionNotFoundError(f"{doc_id} v{version}")

    def fulltext_scores(self, query_text: str) -> dict[str, float]:
        return {
            d.id: 1.0 for d in self._documents.values() if query_text.lower() in d.content.lower()
        }

    def counts(self) -> tuple[int, int, int]:
        versions = [v for items in self._versions.values() for v in items]
        return len(self._documents), len(versions), sum(len(v.content.encode()) for v in versions)

    def ping(self) -> None:
        pass

    @staticmethod
    def _stored(
        doc_id: str, version: int, draft: DocumentDraft, created_at: datetime, updated_at: datetime
    ) -> StoredDocument:
        meta = draft.parsed.metadata
        return StoredDocument(
            id=doc_id,
            version=version,
            title=meta.title,
            tags=meta.tags,
            category=meta.category,
            author=meta.author,
            created=meta.created,
            content=draft.content,
            source_name=draft.source_name,
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _version(stored: StoredDocument, draft: DocumentDraft, now: datetime) -> DocumentVersion:
        return DocumentVersion(
            document_id=stored.id,
            version=stored.version,
            title=stored.title,
            content=draft.content,
            content_hash=content_hash(draft.content),
            created_at=now,
        )
