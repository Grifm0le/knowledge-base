from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import redis
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from fakes import InMemoryRepository, InMemoryUserRepository, MemoryMailer
from kb.api.app import create_app
from kb.cache import Cache, MemoryCache
from kb.config import Settings
from kb.importer import import_directory
from kb.parser import parse_document
from kb.search import SearchEngine
from kb.security import ACCESS, hash_password
from kb.storage.repository import DocumentRepository, SqlDocumentRepository
from kb.storage.users import SqlUserRepository, User, UserRepository

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
IMPORT_SET = ROOT / "tests" / "data" / "import_set"

OWNER = "00000000-0000-4000-8000-000000000001"
OWNER_EMAIL = "owner@example.org"
PASSWORD = "correct-horse-battery"
# Хэширование намеренно медленное: одного вычисления хватает на все тесты.
PASSWORD_HASH = hash_password(PASSWORD)
JWT_SECRET = "тестовый-ключ-подписи-длиной-больше-32-символов"


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


def make_settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        log_level="WARNING",
        jwt_secret=JWT_SECRET,
        cookie_secure=False,
        **overrides,
    )


@pytest.fixture
def repository(pg_engine: Engine) -> SqlDocumentRepository:
    """Пустая база и один пользователь, которому принадлежат документы тестов."""
    with pg_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, email_verified_at, consent_at) "
                "VALUES (:id, :email, :hash, now(), now())"
            ),
            {"id": OWNER, "email": OWNER_EMAIL, "hash": PASSWORD_HASH},
        )
    return SqlDocumentRepository(pg_engine)


@pytest.fixture
def users(pg_engine: Engine) -> SqlUserRepository:
    return SqlUserRepository(pg_engine)


def verified_user(accounts: UserRepository, email: str = OWNER_EMAIL) -> User:
    """Подтверждённая учётная запись: без неё токен не выдать."""
    found = accounts.find_by_email(email)
    if found is not None:
        return found
    now = datetime.now(timezone.utc)
    user = accounts.create(email, PASSWORD_HASH, now)
    accounts.mark_email_verified(user.id, now)
    found = accounts.find_by_email(email)
    assert found is not None
    return found


def bearer(client: TestClient, user: User) -> dict[str, str]:
    """Заголовок с токеном доступа, выпущенным самим приложением."""
    token = client.app.state.auth.tokens.issue(user.id, user.token_version, ACCESS)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client_for() -> Iterator[Callable[..., TestClient]]:
    """Собирает клиент API, уже вошедший как пользователь-владелец."""
    clients: list[TestClient] = []
    memory_users = InMemoryUserRepository()

    def build(
        storage: DocumentRepository,
        cache: Cache | None = None,
        users: UserRepository | None = None,
        mailer: MemoryMailer | None = None,
        **settings: object,
    ) -> TestClient:
        accounts = users or memory_users
        app = create_app(
            make_settings(**settings),
            repository=storage,
            cache=cache or MemoryCache(),
            users=accounts,
            mailer=mailer or MemoryMailer(),
        )
        test_client = TestClient(app)
        test_client.__enter__()
        clients.append(test_client)
        test_client.headers.update(bearer(test_client, verified_user(accounts)))
        return test_client

    yield build
    for test_client in clients:
        test_client.__exit__(None, None, None)


@pytest.fixture
def client(
    client_for: Callable[..., TestClient],
    repository: SqlDocumentRepository,
    users: SqlUserRepository,
) -> TestClient:
    """API на настоящем PostgreSQL и кэше в памяти."""
    return client_for(repository, users=users)


@pytest.fixture
def memory_client(client_for: Callable[..., TestClient]) -> TestClient:
    """API без базы данных: для проверок, которым не важно хранилище."""
    return client_for(InMemoryRepository())


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
