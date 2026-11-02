"""Тестовые заменители внешних зависимостей."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import redis

from kb.errors import (
    DocumentNotFoundError,
    DuplicateSourceError,
    VersionConflictError,
    VersionNotFoundError,
)
from kb.mailer import Mailer, OutgoingMail
from kb.storage.repository import (
    ACCESS_OWNER,
    DocumentDraft,
    DocumentRepository,
    DocumentVersion,
    SaveResult,
    Share,
    StoredDocument,
    content_hash,
)
from kb.storage.users import EmailTakenError, User, UserRepository


class BrokenRedis:
    """Клиент Redis, у которого пропало соединение."""

    def _fail(self, *args: object, **kwargs: object) -> None:
        raise redis.ConnectionError("соединение разорвано")

    get = set = incr = expire = delete = ping = _fail


class InMemoryRepository(DocumentRepository):
    """Хранилище в памяти: проверяет логику сервиса без базы данных."""

    def __init__(self, users: UserRepository | None = None) -> None:
        self._users = users
        self._documents: dict[str, StoredDocument] = {}
        self._versions: dict[str, list[DocumentVersion]] = {}
        self._shares: dict[tuple[str, str], str] = {}

    def create(self, owner_id: str, draft: DocumentDraft) -> StoredDocument:
        if draft.source_name and self.find_by_source(owner_id, draft.source_name):
            raise DuplicateSourceError(draft.source_name)
        now = datetime.now(timezone.utc)
        stored = self._stored(str(uuid4()), owner_id, 1, draft, now, now)
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
        stored = self._stored(
            doc_id, current.owner_id, current.version + 1, draft, current.created_at, now
        )
        self._documents[doc_id] = stored
        self._versions[doc_id].append(self._version(stored, draft, now))
        return SaveResult(stored, changed=True)

    def delete(self, doc_id: str) -> None:
        self.get(doc_id)
        del self._documents[doc_id]
        del self._versions[doc_id]
        self._shares = {key: value for key, value in self._shares.items() if key[0] != doc_id}

    def get(self, doc_id: str) -> StoredDocument:
        try:
            return self._documents[doc_id]
        except KeyError:
            raise DocumentNotFoundError(doc_id) from None

    def find_by_source(self, owner_id: str, source_name: str) -> StoredDocument | None:
        return next(
            (
                d
                for d in self._documents.values()
                if d.owner_id == owner_id and d.source_name == source_name
            ),
            None,
        )

    def list_documents(
        self, user_id: str, limit: int, offset: int
    ) -> tuple[list[StoredDocument], int]:
        items = sorted(self._visible(user_id), key=lambda d: d.updated_at, reverse=True)
        page = [replace(d, access=self.access_level(d.id, user_id)) for d in items]
        return page[offset : offset + limit], len(items)

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

    def fulltext_scores(
        self, user_id: str, query_text: str, field_weights: Mapping[str, float] | None = None
    ) -> dict[str, float]:
        return {
            d.id: 1.0 for d in self._visible(user_id) if query_text.lower() in d.content.lower()
        }

    def counts(self, user_id: str) -> tuple[int, int, int]:
        visible = self._visible(user_id)
        versions = [v for d in visible for v in self._versions[d.id]]
        return len(visible), len(versions), sum(len(v.content.encode()) for v in versions)

    def access_level(self, doc_id: str, user_id: str) -> str | None:
        document = self._documents.get(doc_id)
        if document is None:
            return None
        if document.owner_id == user_id:
            return ACCESS_OWNER
        return self._shares.get((doc_id, user_id))

    def audience(self, doc_id: str) -> list[str]:
        document = self._documents.get(doc_id)
        if document is None:
            return []
        return [document.owner_id, *(user for doc, user in self._shares if doc == doc_id)]

    def set_share(self, doc_id: str, user_id: str, permission: str) -> None:
        self._shares[(doc_id, user_id)] = permission

    def remove_share(self, doc_id: str, user_id: str) -> bool:
        return self._shares.pop((doc_id, user_id), None) is not None

    def list_shares(self, doc_id: str) -> list[Share]:
        return [
            Share(user, self._users.get(user).email if self._users else user, permission)
            for (doc, user), permission in self._shares.items()
            if doc == doc_id
        ]

    def iter_shares(self) -> Iterator[tuple[str, str]]:
        return iter(list(self._shares))

    def owned_document_ids(self, owner_id: str) -> list[str]:
        return [d.id for d in self._documents.values() if d.owner_id == owner_id]

    def _visible(self, user_id: str) -> list[StoredDocument]:
        return [d for d in self._documents.values() if self.access_level(d.id, user_id)]

    def ping(self) -> None:
        pass

    @staticmethod
    def _stored(
        doc_id: str,
        owner_id: str,
        version: int,
        draft: DocumentDraft,
        created_at: datetime,
        updated_at: datetime,
    ) -> StoredDocument:
        meta = draft.parsed.metadata
        return StoredDocument(
            id=doc_id,
            owner_id=owner_id,
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


class InMemoryUserRepository(UserRepository):
    """Учётные записи в памяти."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        # Хэш токена: идентификатор пользователя, цель, срок, погашен ли.
        self.tokens: dict[str, list] = {}

    def create(self, email: str, password_hash: str, consent_at: datetime) -> User:
        if self.find_by_email(email):
            raise EmailTakenError(email)
        now = datetime.now(timezone.utc)
        user = User(str(uuid4()), email, password_hash, 1, None, consent_at, now)
        self._users[user.id] = user
        return user

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def find_by_email(self, email: str) -> User | None:
        return next((u for u in self._users.values() if u.email == email), None)

    def set_password(self, user_id: str, password_hash: str) -> None:
        user = self._users[user_id]
        self._users[user_id] = replace(
            user, password_hash=password_hash, token_version=user.token_version + 1
        )

    def update_password_hash(self, user_id: str, password_hash: str) -> None:
        self._users[user_id] = replace(self._users[user_id], password_hash=password_hash)

    def revoke_tokens(self, user_id: str) -> None:
        user = self._users[user_id]
        self._users[user_id] = replace(user, token_version=user.token_version + 1)

    def mark_email_verified(self, user_id: str, moment: datetime) -> None:
        user = self._users[user_id]
        if user.email_verified_at is None:
            self._users[user_id] = replace(user, email_verified_at=moment)

    def delete(self, user_id: str) -> None:
        self._users.pop(user_id, None)

    def add_one_time_token(
        self, user_id: str, token_hash: str, purpose: str, expires_at: datetime
    ) -> None:
        self.tokens = {
            key: value
            for key, value in self.tokens.items()
            if not (value[0] == user_id and value[1] == purpose)
        }
        self.tokens[token_hash] = [user_id, purpose, expires_at, False]

    def find_one_time_token(self, token_hash: str, purpose: str, now: datetime) -> str | None:
        token = self.tokens.get(token_hash)
        if token is None or token[1] != purpose or token[3] or token[2] <= now:
            return None
        return token[0]

    def consume_one_time_token(self, token_hash: str, purpose: str, now: datetime) -> str | None:
        token = self.tokens.get(token_hash)
        if token is None or token[1] != purpose or token[3] or token[2] <= now:
            return None
        token[3] = True
        return token[0]


class MemoryMailer(Mailer):
    """Складывает письма в список: тест читает из них разовые ссылки."""

    def __init__(self) -> None:
        self.sent: list[OutgoingMail] = []

    def send(self, mail: OutgoingMail) -> None:
        self.sent.append(mail)
