"""Пароли, токены доступа и разовые токены из писем."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from kb.errors import InvalidTokenError, WeakPasswordError

ACCESS = "access"
REFRESH = "refresh"
_ALGORITHM = "HS256"

MIN_PASSWORD_LENGTH = 10
# Ограничение сверху защищает от запросов, на которые хэширование потратит много времени.
MAX_PASSWORD_LENGTH = 128
_COMMON_PASSWORDS = frozenset(
    {
        "1234567890",
        "12345678910",
        "0123456789",
        "1111111111",
        "qwertyuiop",
        "qwerty1234",
        "qwerty12345",
        "password12",
        "password123",
        "password1234",
        "administrator",
        "letmein1234",
        "iloveyou123",
        "йцукенгшщз",
    }
)

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Хэш Argon2id: случайная соль и параметры стоимости хранятся внутри строки."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True, если хэш создан с параметрами слабее текущих."""
    return _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(16))


def waste_password_check(password: str) -> None:
    """Тратит то же время, что и настоящая проверка: по времени нельзя узнать, есть ли адрес."""
    verify_password(_dummy_hash(), password)


def validate_new_password(password: str, email: str) -> None:
    """Правила для нового пароля. Выбрасывает WeakPasswordError с понятной причиной."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Пароль короче {MIN_PASSWORD_LENGTH} символов")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Пароль длиннее {MAX_PASSWORD_LENGTH} символов")
    if password.casefold() in _COMMON_PASSWORDS:
        raise WeakPasswordError("Этот пароль слишком распространён")
    if len(set(password)) < 5:
        raise WeakPasswordError("В пароле слишком мало разных символов")
    local_part = email.split("@", 1)[0].casefold()
    if len(local_part) >= 4 and local_part in password.casefold():
        raise WeakPasswordError("Пароль не должен содержать адрес электронной почты")


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: str
    token_version: int
    kind: str


class TokenService:
    """Выпускает и проверяет JWT. В токене нет персональных данных, только идентификатор."""

    def __init__(
        self,
        secret: str,
        access_seconds: int,
        refresh_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._secret = secret
        self._lifetimes = {ACCESS: access_seconds, REFRESH: refresh_seconds}
        self._clock = clock

    @property
    def access_seconds(self) -> int:
        return self._lifetimes[ACCESS]

    @property
    def refresh_seconds(self) -> int:
        return self._lifetimes[REFRESH]

    def issue(self, user_id: str, token_version: int, kind: str) -> str:
        now = int(self._clock())
        claims = {
            "sub": user_id,
            "tv": token_version,
            "typ": kind,
            "iat": now,
            "exp": now + self._lifetimes[kind],
            "jti": secrets.token_urlsafe(8),
        }
        return jwt.encode(claims, self._secret, algorithm=_ALGORITHM)

    def decode(self, token: str, kind: str) -> TokenClaims:
        """Проверяет подпись, алгоритм, срок и назначение токена."""
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                options={"require": ["sub", "tv", "typ", "exp"], "verify_exp": False},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("Токен недействителен") from exc
        # Срок проверяется здесь, а не в библиотеке: так время можно подменить в тестах.
        if claims["exp"] <= self._clock():
            raise InvalidTokenError("Срок действия токена истёк")
        if claims["typ"] != kind:
            raise InvalidTokenError("Токен предназначен для другого действия")
        return TokenClaims(claims["sub"], claims["tv"], kind)


def new_one_time_token() -> tuple[str, str]:
    """Случайный токен для письма и его хэш для базы: утечка базы не даст готовых ссылок."""
    token = secrets.token_urlsafe(32)
    return token, hash_one_time_token(token)


def hash_one_time_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def mask_email(email: str) -> str:
    """Адрес для журнала: персональные данные не должны попадать в файлы журнала."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"
