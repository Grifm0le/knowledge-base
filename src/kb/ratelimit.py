"""Ограничение числа попыток: защита входа и восстановления пароля от перебора."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from kb.cache import Cache
from kb.errors import TooManyRequestsError


@dataclass(frozen=True, slots=True)
class Limit:
    attempts: int
    window_seconds: int


class RateLimiter:
    """Счётчики попыток в кэше. Адреса хранятся как хэши, чтобы в Redis не было персональных данных.

    Если Redis недоступен, ограничение не действует: сервис остаётся доступным, а пароли защищены
    стойким хэшем. Такой выбор отражён в журнале предупреждением кэша.
    """

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    def check(self, scope: str, value: str, limit: Limit) -> None:
        count = self._cache.get(self._key(scope, value))
        if count is not None and int(count) >= limit.attempts:
            raise TooManyRequestsError(
                "Слишком много попыток, повторите позже", limit.window_seconds
            )

    def hit(self, scope: str, value: str, limit: Limit) -> None:
        self._cache.count_hit(self._key(scope, value), limit.window_seconds)

    def reset(self, scope: str, value: str) -> None:
        self._cache.delete(self._key(scope, value))

    @staticmethod
    def _key(scope: str, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
        return f"kb:rate:{scope}:{digest}"
