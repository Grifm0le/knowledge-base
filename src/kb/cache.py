"""Кэш результатов поиска. Недоступность кэша не должна ломать поиск."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from threading import Lock
from typing import Any

import redis

logger = logging.getLogger(__name__)

GENERATION_KEY = "kb:search:generation"


class Cache(ABC):
    """Минимальный интерфейс хранилища ключей. Ошибки соединения реализация обрабатывает сама."""

    @abstractmethod
    def get(self, key: str) -> str | None:
        """Значение ключа или None, если его нет."""

    @abstractmethod
    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Сохраняет значение на заданное время."""

    @abstractmethod
    def increment(self, key: str) -> None:
        """Увеличивает счётчик на единицу."""

    @abstractmethod
    def count_hit(self, key: str, ttl_seconds: int) -> int:
        """Увеличивает счётчик с ограниченным сроком жизни и возвращает его значение."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Удаляет ключ."""

    @abstractmethod
    def ping(self) -> bool:
        """Доступен ли кэш."""


class NullCache(Cache):
    """Кэш отключён: сервис работает так же, только без ускорения."""

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        pass

    def increment(self, key: str) -> None:
        pass

    def count_hit(self, key: str, ttl_seconds: int) -> int:
        return 0

    def delete(self, key: str) -> None:
        pass

    def ping(self) -> bool:
        return True


class MemoryCache(Cache):
    """Кэш в памяти процесса: для тестов и запуска без Redis."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._items: dict[str, tuple[str, float | None]] = {}
        self._lock = Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            value, expires_at = item
            if expires_at is not None and expires_at <= self._clock():
                del self._items[key]
                return None
            return value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            self._items[key] = (value, self._clock() + ttl_seconds)

    def increment(self, key: str) -> None:
        with self._lock:
            value, _ = self._items.get(key, ("0", None))
            self._items[key] = (str(int(value) + 1), None)

    def count_hit(self, key: str, ttl_seconds: int) -> int:
        current = self.get(key)
        with self._lock:
            if current is None:
                self._items[key] = ("1", self._clock() + ttl_seconds)
                return 1
            _, expires_at = self._items[key]
            value = int(current) + 1
            self._items[key] = (str(value), expires_at)
            return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def ping(self) -> bool:
        return True


class RedisCache(Cache):
    """Адаптер Redis. При сбое пишет предупреждение и ведёт себя как пустой кэш."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str, timeout_seconds: float) -> RedisCache:
        client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=timeout_seconds,
            socket_connect_timeout=timeout_seconds,
        )
        return cls(client)

    def get(self, key: str) -> str | None:
        try:
            value: str | None = self._client.get(key)  # type: ignore[assignment]
        except redis.RedisError as exc:
            logger.warning("Redis недоступен при чтении: %s", exc)
            return None
        return value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            self._client.set(key, value, ex=ttl_seconds)
        except redis.RedisError as exc:
            logger.warning("Redis недоступен при записи: %s", exc)

    def increment(self, key: str) -> None:
        try:
            self._client.incr(key)
        except redis.RedisError as exc:
            logger.warning("Redis недоступен при сбросе кэша: %s", exc)

    def count_hit(self, key: str, ttl_seconds: int) -> int:
        try:
            value = int(self._client.incr(key))
            if value == 1:
                self._client.expire(key, ttl_seconds)
        except redis.RedisError as exc:
            logger.warning("Redis недоступен при подсчёте попыток: %s", exc)
            return 0
        return value

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except redis.RedisError as exc:
            logger.warning("Redis недоступен при удалении ключа: %s", exc)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except redis.RedisError:
            return False


class SearchCache:
    """Кэш выдачи пользователя. Смена поколения разом отменяет записи пользователя.

    Общий номер меняется при запуске сервиса: данные могли измениться, пока сервис не работал.
    """

    def __init__(self, cache: Cache, ttl_seconds: int) -> None:
        self._cache = cache
        self._ttl_seconds = ttl_seconds
        self.hits = 0
        self.misses = 0

    def key_for(self, user_id: str, params: Mapping[str, Any]) -> str:
        """Один и тот же запрос даёт один ключ независимо от порядка параметров."""
        epoch = self._cache.get(GENERATION_KEY) or "0"
        generation = self._cache.get(_user_generation_key(user_id)) or "0"
        canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"kb:search:{epoch}:{user_id}:{generation}:{digest}"

    def get(self, key: str) -> dict[str, Any] | None:
        value = self._cache.get(key)
        if value is None:
            self.misses += 1
            return None
        self.hits += 1
        payload: dict[str, Any] = json.loads(value)
        return payload

    def put(self, key: str, payload: Mapping[str, Any]) -> None:
        self._cache.set(key, json.dumps(payload, ensure_ascii=False), self._ttl_seconds)

    def invalidate(self, user_id: str) -> None:
        self._cache.increment(_user_generation_key(user_id))

    def invalidate_all(self) -> None:
        self._cache.increment(GENERATION_KEY)

    def ping(self) -> bool:
        return self._cache.ping()


def _user_generation_key(user_id: str) -> str:
    return f"kb:search:user:{user_id}:generation"
