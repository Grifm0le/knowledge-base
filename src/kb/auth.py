"""Аутентификация: регистрация, вход, токены, восстановление пароля и удаление учётной записи."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kb.errors import (
    AuthenticationError,
    ConsentRequiredError,
    EmailNotVerifiedError,
    InvalidTokenError,
    OneTimeTokenError,
)
from kb.mailer import OutgoingMail
from kb.ratelimit import Limit, RateLimiter
from kb.security import (
    ACCESS,
    REFRESH,
    TokenService,
    hash_one_time_token,
    hash_password,
    mask_email,
    needs_rehash,
    new_one_time_token,
    normalize_email,
    validate_new_password,
    verify_password,
    waste_password_check,
)
from kb.storage.users import EmailTakenError, User, UserRepository

logger = logging.getLogger(__name__)

VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"


LOGIN_BY_EMAIL = Limit(5, 15 * 60)
LOGIN_BY_ADDRESS = Limit(30, 15 * 60)
RECOVERY_BY_EMAIL = Limit(3, 60 * 60)
RECOVERY_BY_ADDRESS = Limit(10, 60 * 60)
REGISTER_BY_ADDRESS = Limit(10, 60 * 60)


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str


class AuthService:
    """Правила входа. Письма не отправляет: возвращает их, чтобы отправка шла после ответа."""

    def __init__(
        self,
        users: UserRepository,
        tokens: TokenService,
        limiter: RateLimiter,
        public_url: str,
        one_time_token_minutes: int = 30,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._users = users
        self._tokens = tokens
        self._limiter = limiter
        self._public_url = public_url.rstrip("/")
        self._one_time_minutes = one_time_token_minutes
        self._now = now

    @property
    def tokens(self) -> TokenService:
        return self._tokens

    def register(self, email: str, password: str, consent: bool, address: str) -> OutgoingMail:
        """Ответ одинаков, занят адрес или нет: иначе чужие адреса можно проверять перебором."""
        email = normalize_email(email)
        if not consent:
            raise ConsentRequiredError("Нужно согласие на обработку адреса электронной почты")
        validate_new_password(password, email)
        self._limiter.check("register-address", address, REGISTER_BY_ADDRESS)
        self._limiter.hit("register-address", address, REGISTER_BY_ADDRESS)
        password_hash = hash_password(password)
        try:
            user = self._users.create(email, password_hash, self._now())
        except EmailTakenError:
            logger.info("Повторная регистрация адреса %s", mask_email(email))
            return self._repeated_registration_mail(email)
        logger.info("Зарегистрирован пользователь %s", user.id)
        return self._verification_mail(user.id, email)

    def verify_email(self, token: str) -> None:
        user_id = self._consume(token, VERIFY_EMAIL)
        self._users.mark_email_verified(user_id, self._now())
        logger.info("Адрес пользователя %s подтверждён", user_id)

    def login(self, email: str, password: str, address: str) -> TokenPair:
        email = normalize_email(email)
        self._limiter.check("login-email", email, LOGIN_BY_EMAIL)
        self._limiter.check("login-address", address, LOGIN_BY_ADDRESS)
        user = self._users.find_by_email(email)
        if user is None:
            waste_password_check(password)
        elif verify_password(user.password_hash, password):
            if user.email_verified_at is None:
                raise EmailNotVerifiedError("Сначала подтвердите адрес по ссылке из письма")
            self._limiter.reset("login-email", email)
            if needs_rehash(user.password_hash):
                self._users.update_password_hash(user.id, hash_password(password))
            logger.info("Вход пользователя %s", user.id)
            return self._pair(user)
        self._limiter.hit("login-email", email, LOGIN_BY_EMAIL)
        self._limiter.hit("login-address", address, LOGIN_BY_ADDRESS)
        logger.warning("Неудачный вход с адресом %s", mask_email(email))
        raise AuthenticationError("Неверный адрес или пароль")

    def refresh(self, refresh_token: str) -> TokenPair:
        return self._pair(self._user_from(refresh_token, REFRESH))

    def authenticate(self, access_token: str) -> User:
        return self._user_from(access_token, ACCESS)

    def logout(self, user: User) -> None:
        """Отзывает все токены пользователя, в том числе на других устройствах."""
        self._users.revoke_tokens(user.id)
        logger.info("Выход пользователя %s", user.id)

    def change_password(self, user: User, current_password: str, new_password: str) -> TokenPair:
        if not verify_password(user.password_hash, current_password):
            raise AuthenticationError("Текущий пароль указан неверно")
        validate_new_password(new_password, user.email)
        self._users.set_password(user.id, hash_password(new_password))
        logger.info("Пользователь %s сменил пароль", user.id)
        updated = self._users.get(user.id)
        if updated is None:
            raise InvalidTokenError("Учётная запись удалена")
        return self._pair(updated)

    def forgot_password(self, email: str, address: str) -> OutgoingMail | None:
        """Письмо уходит, только если адрес есть, но ответ клиенту от этого не зависит."""
        email = normalize_email(email)
        self._limiter.check("recovery-address", address, RECOVERY_BY_ADDRESS)
        self._limiter.check("recovery-email", email, RECOVERY_BY_EMAIL)
        self._limiter.hit("recovery-address", address, RECOVERY_BY_ADDRESS)
        self._limiter.hit("recovery-email", email, RECOVERY_BY_EMAIL)
        user = self._users.find_by_email(email)
        if user is None:
            logger.info("Восстановление пароля для неизвестного адреса %s", mask_email(email))
            return None
        link = self._one_time_link(user.id, RESET_PASSWORD, "reset")
        logger.info("Выдана ссылка восстановления пароля пользователю %s", user.id)
        return OutgoingMail(
            email,
            "Восстановление пароля",
            f"Для смены пароля откройте ссылку: {link}\n"
            f"Ссылка действует {self._one_time_minutes} минут и работает один раз.\n"
            "Если вы не запрашивали смену пароля, проигнорируйте письмо.",
        )

    def reset_password(self, token: str, new_password: str) -> None:
        user_id = self._consume(token, RESET_PASSWORD)
        user = self._users.get(user_id)
        if user is None:
            raise OneTimeTokenError("Ссылка недействительна")
        validate_new_password(new_password, user.email)
        self._users.set_password(user_id, hash_password(new_password))
        # Письмо пришло на этот адрес, значит владелец подтвердил его заодно.
        self._users.mark_email_verified(user_id, self._now())
        self._limiter.reset("login-email", user.email)
        logger.info("Пользователь %s восстановил пароль", user_id)

    def check_password(self, user: User, password: str) -> None:
        if not verify_password(user.password_hash, password):
            raise AuthenticationError("Пароль указан неверно")

    def _verification_mail(self, user_id: str, email: str) -> OutgoingMail:
        link = self._one_time_link(user_id, VERIFY_EMAIL, "verify")
        return OutgoingMail(
            email,
            "Подтверждение адреса",
            f"Для подтверждения адреса откройте ссылку: {link}\n"
            f"Ссылка действует {self._one_time_minutes} минут.",
        )

    def _repeated_registration_mail(self, email: str) -> OutgoingMail:
        existing = self._users.find_by_email(email)
        if existing is not None and existing.email_verified_at is None:
            # Первое письмо могло потеряться или устареть: повторная попытка выдаёт новую ссылку.
            return self._verification_mail(existing.id, email)
        return OutgoingMail(
            email,
            "Попытка повторной регистрации",
            "Кто-то пытался зарегистрироваться с этим адресом, но он уже занят.\n"
            f"Если это были вы, войдите в систему: {self._public_url}/#/login\n"
            "Если нет, просто проигнорируйте письмо.",
        )

    def _pair(self, user: User) -> TokenPair:
        return TokenPair(
            self._tokens.issue(user.id, user.token_version, ACCESS),
            self._tokens.issue(user.id, user.token_version, REFRESH),
        )

    def _user_from(self, token: str, kind: str) -> User:
        claims = self._tokens.decode(token, kind)
        user = self._users.get(claims.user_id)
        if user is None or user.token_version != claims.token_version:
            raise InvalidTokenError("Токен отозван")
        return user

    def _one_time_link(self, user_id: str, purpose: str, page: str) -> str:
        token, token_hash = new_one_time_token()
        self._users.add_one_time_token(
            user_id, token_hash, purpose, self._now() + timedelta(minutes=self._one_time_minutes)
        )
        # Токен стоит после знака #: эта часть адреса не попадает в журналы серверов.
        return f"{self._public_url}/#/{page}?token={token}"

    def _consume(self, token: str, purpose: str) -> str:
        user_id = self._users.consume_one_time_token(
            hash_one_time_token(token), purpose, self._now()
        )
        if user_id is None:
            raise OneTimeTokenError("Ссылка недействительна, просрочена или уже использована")
        return user_id
