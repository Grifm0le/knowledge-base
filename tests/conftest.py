from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import redis
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from kb.api.app import create_app
from kb.cache import Cache, MemoryCache
from kb.config import Settings
from kb.importer import import_directory
from kb.parser import parse_document
from kb.search import SearchEngine
from kb.storage.repository import DocumentRepository, SqlDocumentRepository

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
IMPORT_SET = ROOT / "tests" / "data" / "import_set"


@pytest.fixture
def engine_from() -> Callable[[dict[str, str]], SearchEngine]:
    """Собирает движок из словаря «идентификатор → Markdown»."""

    def build(documents: dict[str, str]) -> SearchEngine:
        engine = SearchEngine()
        for doc_id, text_ in documents.items():
            engine.add_document(doc_id, parse_document(text_))
        return engine

    return build


@pytest.fixture
def samples_engine() -> SearchEngine:
    """Движок с реальными примерами документов из каталога samples."""
    engine = SearchEngine()
    report = import_directory(engine, SAMPLES)
    assert not report.failed
    return engine


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("KB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("KB_TEST_DATABASE_URL не задан")
    return url


@pytest.fixture(scope="session")
def pg_engine(database_url: str) -> Iterator[Engine]:
    """Настоящий PostgreSQL. Схема создаётся теми же миграциями, что и в работе."""
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(pg_engine: Engine) -> SqlDocumentRepository:
    with pg_engine.begin() as connection:
        connection.execute(text("TRUNCATE documents CASCADE"))
    return SqlDocumentRepository(pg_engine)


@pytest.fixture
def client_for() -> Iterator[Callable[..., TestClient]]:
    """Собирает клиент API над заданным хранилищем и кэшем."""
    clients: list[TestClient] = []

    def build(storage: DocumentRepository, cache: Cache | None = None) -> TestClient:
        settings = Settings(_env_file=None, log_level="WARNING")
        app = create_app(settings, repository=storage, cache=cache or MemoryCache())
        test_client = TestClient(app)
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield build
    for test_client in clients:
        test_client.__exit__(None, None, None)


@pytest.fixture
def client(client_for: Callable[..., TestClient], repository: SqlDocumentRepository) -> TestClient:
    """API на настоящем PostgreSQL и кэше в памяти."""
    return client_for(repository)


@pytest.fixture
def live_redis() -> Iterator[redis.Redis]:
    """Настоящий Redis, отдельная база очищается перед каждым тестом."""
    url = os.environ.get("KB_TEST_REDIS_URL")
    if not url:
        pytest.skip("KB_TEST_REDIS_URL не задан")
    connection = redis.Redis.from_url(url, decode_responses=True)
    connection.flushdb()
    yield connection
    connection.flushdb()
    connection.close()
