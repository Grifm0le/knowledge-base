"""Сборка приложения FastAPI: зависимости, журнал и единый формат ошибок.

Запуск: uvicorn --factory kb.api.app:create_app
Индекс хранится в памяти процесса, поэтому сервис запускается с одним рабочим процессом.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from kb import __version__
from kb.api.auth_routes import router as auth_router
from kb.api.routes import public_router, router
from kb.auth import AuthService
from kb.cache import Cache, MemoryCache, NullCache, RedisCache, SearchCache
from kb.config import Settings
from kb.errors import (
    AccessDeniedError,
    AuthenticationError,
    ConsentRequiredError,
    DocumentNotFoundError,
    DocumentValidationError,
    DuplicateSourceError,
    EmailNotVerifiedError,
    InvalidQueryError,
    InvalidTokenError,
    KnowledgeBaseError,
    OneTimeTokenError,
    TooManyRequestsError,
    UnknownScorerError,
    UserNotFoundError,
    VersionConflictError,
    VersionNotFoundError,
    WeakPasswordError,
)
from kb.mailer import LogMailer, Mailer, SmtpMailer
from kb.ratelimit import RateLimiter
from kb.security import TokenService
from kb.service import KnowledgeBase
from kb.storage.repository import DocumentRepository, SqlDocumentRepository
from kb.storage.users import SqlUserRepository, UserRepository

logger = logging.getLogger(__name__)

ERROR_STATUS: dict[type[KnowledgeBaseError], int] = {
    DocumentValidationError: 422,
    DocumentNotFoundError: 404,
    VersionNotFoundError: 404,
    VersionConflictError: 409,
    DuplicateSourceError: 409,
    UnknownScorerError: 400,
    InvalidQueryError: 400,
    AuthenticationError: 401,
    InvalidTokenError: 401,
    OneTimeTokenError: 400,
    EmailNotVerifiedError: 403,
    AccessDeniedError: 403,
    UserNotFoundError: 404,
    WeakPasswordError: 422,
    ConsentRequiredError: 422,
    TooManyRequestsError: 429,
}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
# Сторонние скрипты запрещены: даже пропущенная вставка HTML не сможет выполнить код.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)
# Страницы документации загружают файлы с внешнего адреса, поэтому правило к ним не применяется.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def configure_logging(level: str, sql_echo: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s")
    )
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    # На уровне DEBUG библиотека пишет сообщение на каждое правило разбора и забивает журнал.
    logging.getLogger("markdown_it").setLevel(logging.WARNING)
    # Параметр echo движка добавляет свой обработчик, и каждая строка попадала в журнал дважды.
    if sql_echo:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)


def _error(status_code: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message, **extra}}
    )


def _add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(KnowledgeBaseError)
    async def handle_domain_error(_: Request, exc: KnowledgeBaseError) -> JSONResponse:
        response = _error(ERROR_STATUS.get(type(exc), 500), exc.code, str(exc))
        if isinstance(exc, InvalidTokenError):
            response.headers["WWW-Authenticate"] = "Bearer"
        if isinstance(exc, TooManyRequestsError):
            response.headers["Retry-After"] = str(exc.retry_after)
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_bad_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return _error(422, "invalid_request", "Некорректные параметры запроса", details=details)

    @app.exception_handler(OperationalError)
    async def handle_database_down(_: Request, exc: OperationalError) -> JSONResponse:
        logger.error("База данных недоступна: %s", exc.orig)
        return _error(503, "database_unavailable", "База данных недоступна")


def _add_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid4().hex[:12]
        token = _request_id.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed = (time.perf_counter() - started) * 1000
            logger.info(
                "%s %s -> %d, %.1f мс",
                request.method,
                request.url.path,
                response.status_code,
                elapsed,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _request_id.reset(token)


def _add_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def set_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        path = request.url.path
        if not path.startswith(_DOCS_PATHS):
            response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
        if path.startswith("/api/"):
            # Ответы с токенами и документами не должны оставаться в кэше браузера и прокси.
            response.headers["Cache-Control"] = "no-store"
        return response


def _build_cache(settings: Settings) -> Cache:
    if not settings.redis_url:
        logger.warning("KB_REDIS_URL не задан: результаты поиска не кэшируются")
        return NullCache()
    return RedisCache.from_url(settings.redis_url, settings.cache_timeout_seconds)


def _build_mailer(settings: Settings) -> Mailer:
    if not settings.smtp_host:
        logger.warning("KB_SMTP_HOST не задан: письма пишутся в журнал, только для разработки")
        return LogMailer()
    password = settings.smtp_password.get_secret_value() if settings.smtp_password else None
    return SmtpMailer(
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_from,
        settings.smtp_user,
        password,
        settings.smtp_starttls,
    )


def create_app(
    settings: Settings | None = None,
    repository: DocumentRepository | None = None,
    cache: Cache | None = None,
    users: UserRepository | None = None,
    mailer: Mailer | None = None,
) -> FastAPI:
    """Собирает приложение. Хранилища, кэш и почту можно подменить, например в тестах."""
    config = settings or Settings()  # type: ignore[call-arg]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(config.log_level, config.sql_echo)
        engine = create_engine(config.database_url, pool_pre_ping=True)
        storage = repository or SqlDocumentRepository(engine)
        accounts = users or SqlUserRepository(engine)
        raw_cache = cache or _build_cache(config)
        search_cache = SearchCache(raw_cache, config.cache_ttl_seconds)
        # Без Redis выдача не кэшируется, а попытки входа всё равно считаются в памяти процесса.
        limiter = RateLimiter(MemoryCache() if isinstance(raw_cache, NullCache) else raw_cache)
        knowledge_base = KnowledgeBase(storage, accounts, search_cache, limiter)
        knowledge_base.load_index()
        app.state.settings = config
        app.state.knowledge_base = knowledge_base
        app.state.mailer = mailer or _build_mailer(config)
        app.state.auth = AuthService(
            accounts,
            TokenService(
                config.jwt_secret.get_secret_value(),
                config.access_token_minutes * 60,
                config.refresh_token_days * 24 * 60 * 60,
            ),
            limiter,
            config.public_url,
            config.one_time_token_minutes,
        )
        yield
        engine.dispose()

    app = FastAPI(
        title="Личная база знаний",
        description="Импорт Markdown, версии документов, ранжирование и полнотекстовый поиск.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if config.docs_enabled else None,
        redoc_url="/redoc" if config.docs_enabled else None,
        openapi_url="/openapi.json" if config.docs_enabled else None,
    )
    _add_error_handlers(app)
    _add_security_headers(app)
    _add_request_logging(app)
    app.include_router(auth_router)
    app.include_router(public_router)
    app.include_router(router)
    return app
