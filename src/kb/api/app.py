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
from kb.api.routes import router
from kb.cache import Cache, NullCache, RedisCache, SearchCache
from kb.config import Settings
from kb.errors import (
    DocumentNotFoundError,
    DocumentValidationError,
    DuplicateSourceError,
    InvalidQueryError,
    KnowledgeBaseError,
    UnknownScorerError,
    VersionConflictError,
    VersionNotFoundError,
)
from kb.search import SearchEngine
from kb.service import KnowledgeBase
from kb.storage.repository import DocumentRepository, SqlDocumentRepository

logger = logging.getLogger(__name__)

ERROR_STATUS: dict[type[KnowledgeBaseError], int] = {
    DocumentValidationError: 422,
    DocumentNotFoundError: 404,
    VersionNotFoundError: 404,
    VersionConflictError: 409,
    DuplicateSourceError: 409,
    UnknownScorerError: 400,
    InvalidQueryError: 400,
}

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
        return _error(ERROR_STATUS.get(type(exc), 500), exc.code, str(exc))

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


def _build_cache(settings: Settings) -> Cache:
    if not settings.redis_url:
        logger.warning("KB_REDIS_URL не задан: результаты поиска не кэшируются")
        return NullCache()
    return RedisCache.from_url(settings.redis_url, settings.cache_timeout_seconds)


def create_app(
    settings: Settings | None = None,
    repository: DocumentRepository | None = None,
    cache: Cache | None = None,
) -> FastAPI:
    """Собирает приложение. Хранилище и кэш можно подменить, например в тестах."""
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(config.log_level, config.sql_echo)
        engine = None
        storage = repository
        if storage is None:
            engine = create_engine(config.database_url, pool_pre_ping=True)
            storage = SqlDocumentRepository(engine)
        search_cache = SearchCache(cache or _build_cache(config), config.cache_ttl_seconds)
        knowledge_base = KnowledgeBase(storage, SearchEngine(), search_cache)
        knowledge_base.load_index()
        app.state.knowledge_base = knowledge_base
        yield
        if engine is not None:
            engine.dispose()

    app = FastAPI(
        title="Личная база знаний",
        description="Импорт Markdown, версии документов, ранжирование и полнотекстовый поиск.",
        version=__version__,
        lifespan=lifespan,
    )
    _add_error_handlers(app)
    _add_request_logging(app)
    app.include_router(router)
    return app
