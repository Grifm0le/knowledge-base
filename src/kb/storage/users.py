"""Учётные записи и разовые токены: интерфейс хранилища и реализация на PostgreSQL."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from kb.storage.tables import AuthTokenRow, UserRow


class EmailTakenError(Exception):
    """Адрес уже зарегистрирован. Наружу не передаётся: сервис отвечает одинаково."""


@dataclass(frozen=True, slots=True)
class User:
    id: str
    email: str
    password_hash: str
    token_version: int
    email_verified_at: datetime | None
    consent_at: datetime
    created_at: datetime


class UserRepository(ABC):
    """Что сервису аутентификации нужно от хранилища."""

    @abstractmethod
    def create(self, email: str, password_hash: str, consent_at: datetime) -> User:
        """Создаёт учётную запись. Если адрес занят, выбрасывает EmailTakenError."""

    @abstractmethod
    def get(self, user_id: str) -> User | None:
        """Учётная запись по идентификатору."""

    @abstractmethod
    def find_by_email(self, email: str) -> User | None:
        """Учётная запись по нормализованному адресу."""

    @abstractmethod
    def set_password(self, user_id: str, password_hash: str) -> None:
        """Сохраняет новый хэш пароля и отзывает все выданные токены."""

    @abstractmethod
    def update_password_hash(self, user_id: str, password_hash: str) -> None:
        """Заменяет хэш того же пароля, например при усилении параметров. Токены остаются."""

    @abstractmethod
    def revoke_tokens(self, user_id: str) -> None:
        """Увеличивает номер версии токенов: все выданные токены перестают действовать."""

    @abstractmethod
    def mark_email_verified(self, user_id: str, moment: datetime) -> None:
        """Отмечает адрес подтверждённым."""

    @abstractmethod
    def delete(self, user_id: str) -> None:
        """Удаляет учётную запись и всё, что от неё зависит."""

    @abstractmethod
    def add_one_time_token(
        self, user_id: str, token_hash: str, purpose: str, expires_at: datetime
    ) -> None:
        """Сохраняет хэш разового токена. Прежние токены с той же целью перестают действовать."""

    @abstractmethod
    def consume_one_time_token(self, token_hash: str, purpose: str, now: datetime) -> str | None:
        """Погашает токен и возвращает идентификатор пользователя или None, если не подходит."""


class SqlUserRepository(UserRepository):
    """Учётные записи в PostgreSQL."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def create(self, email: str, password_hash: str, consent_at: datetime) -> User:
        row = UserRow(
            id=str(uuid4()), email=email, password_hash=password_hash, consent_at=consent_at
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
                session.flush()
                session.refresh(row)
                return _user(row)
        except IntegrityError as exc:
            raise EmailTakenError(email) from exc

    def get(self, user_id: str) -> User | None:
        with self._sessions() as session:
            row = session.get(UserRow, user_id)
            return _user(row) if row else None

    def find_by_email(self, email: str) -> User | None:
        with self._sessions() as session:
            row = session.scalar(select(UserRow).where(UserRow.email == email))
            return _user(row) if row else None

    def set_password(self, user_id: str, password_hash: str) -> None:
        statement = (
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(password_hash=password_hash, token_version=UserRow.token_version + 1)
        )
        with self._sessions.begin() as session:
            session.execute(statement)

    def update_password_hash(self, user_id: str, password_hash: str) -> None:
        statement = update(UserRow).where(UserRow.id == user_id).values(password_hash=password_hash)
        with self._sessions.begin() as session:
            session.execute(statement)

    def revoke_tokens(self, user_id: str) -> None:
        statement = (
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(token_version=UserRow.token_version + 1)
        )
        with self._sessions.begin() as session:
            session.execute(statement)

    def mark_email_verified(self, user_id: str, moment: datetime) -> None:
        statement = (
            update(UserRow)
            .where(UserRow.id == user_id, UserRow.email_verified_at.is_(None))
            .values(email_verified_at=moment)
        )
        with self._sessions.begin() as session:
            session.execute(statement)

    def delete(self, user_id: str) -> None:
        with self._sessions.begin() as session:
            session.execute(delete(UserRow).where(UserRow.id == user_id))

    def add_one_time_token(
        self, user_id: str, token_hash: str, purpose: str, expires_at: datetime
    ) -> None:
        with self._sessions.begin() as session:
            session.execute(
                delete(AuthTokenRow).where(
                    AuthTokenRow.user_id == user_id, AuthTokenRow.purpose == purpose
                )
            )
            session.add(
                AuthTokenRow(
                    token_hash=token_hash, user_id=user_id, purpose=purpose, expires_at=expires_at
                )
            )

    def consume_one_time_token(self, token_hash: str, purpose: str, now: datetime) -> str | None:
        # Одним запросом: два одновременных обращения не смогут использовать токен дважды.
        statement = (
            update(AuthTokenRow)
            .where(
                AuthTokenRow.token_hash == token_hash,
                AuthTokenRow.purpose == purpose,
                AuthTokenRow.used_at.is_(None),
                AuthTokenRow.expires_at > now,
            )
            .values(used_at=func.now())
            .returning(AuthTokenRow.user_id)
        )
        with self._sessions.begin() as session:
            return session.scalar(statement)


def _user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        token_version=row.token_version,
        email_verified_at=row.email_verified_at,
        consent_at=row.consent_at,
        created_at=row.created_at,
    )
