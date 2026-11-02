"""Сценарии работы пользователя через API: каждый проверяет несколько модулей вместе."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fakeredis
import pytest
from sqlalchemy.exc import OperationalError

from conftest import IMPORT_SET, OWNER, SAMPLES
from fakes import BrokenRedis, InMemoryRepository
from kb.cache import RedisCache

pytestmark = pytest.mark.postgres

URL = "/api/v1"
EXPECTED_FAILURES = {
    "bad_date.md": "invalid_date",
    "bad_yaml.md": "invalid_front_matter",
    "cp1251.md": "invalid_encoding",
    "empty.md": "empty_document",
    "notes.txt": "unsupported_file_type",
    "wrong_types.md": "invalid_tags",
}


def upload(paths: list[Path]) -> list[tuple]:
    return [("files", (path.name, path.read_bytes(), "text/markdown")) for path in paths]


def search_titles(client, text, **params):
    body = client.get(f"{URL}/search", params={"q": text, **params}).json()
    return [hit["title"] for hit in body["hits"]]


def test_s1_document_life_cycle(client):
    """Создание, поиск, правка, возврат версии, удаление."""
    created = client.post(
        f"{URL}/documents", json={"content": "# Заметка\n\nСтарый текст о кэше."}
    ).json()
    url = f"{URL}/documents/{created['id']}"
    assert search_titles(client, "кэш") == ["Заметка"]

    client.put(url, json={"content": "# Заметка\n\nНовый текст об индексах."})
    assert search_titles(client, "кэш") == []
    assert search_titles(client, "индекс") == ["Заметка"]

    client.post(f"{url}/versions/1/restore")
    assert search_titles(client, "кэш") == ["Заметка"]
    assert [v["version"] for v in client.get(f"{url}/versions").json()] == [3, 2, 1]

    assert client.delete(url).status_code == 204
    assert search_titles(client, "кэш") == []


def test_s2_import_set_with_bad_files(client):
    """Файлы с ошибками: хорошие приняты, по плохим понятна причина, повтор ничего не меняет."""
    paths = sorted(IMPORT_SET.iterdir())
    first = client.post(f"{URL}/documents/import", files=upload(paths)).json()
    assert sorted(first["created"]) == ["good_full.md", "good_no_metadata.md"]
    assert {f["name"]: f["code"] for f in first["failed"]} == EXPECTED_FAILURES

    second = client.post(f"{URL}/documents/import", files=upload(paths)).json()
    assert second["created"] == []
    assert sorted(second["unchanged"]) == ["good_full.md", "good_no_metadata.md"]
    assert client.get(f"{URL}/stats").json()["versions"] == 2


def test_s3_samples_are_found_by_every_method(client):
    """Демонстрационные заметки: нужный документ первым у каждого метода."""
    report = client.post(
        f"{URL}/documents/import", files=upload(sorted(SAMPLES.glob("*.md")))
    ).json()
    assert len(report["created"]) == 8
    for scorer in ("bm25", "tfidf", "hybrid", "pgfts"):
        assert (
            search_titles(client, "индексы в базе данных", scorer=scorer)[0]
            == "Индексы в PostgreSQL"
        )
        assert search_titles(client, "кэш redis", scorer=scorer)[0] == "Кэширование в Redis"


def test_s4_cache_is_used_and_reset_by_changes(client):
    """Повторный запрос берётся из кэша, любая правка показывает свежий результат."""
    created = client.post(f"{URL}/documents", json={"content": "# Первая\n\nТекст о кэше."}).json()
    params = {"q": "кэш"}
    assert client.get(f"{URL}/search", params=params).headers["X-Cache"] == "MISS"
    assert client.get(f"{URL}/search", params=params).headers["X-Cache"] == "HIT"

    client.post(f"{URL}/documents", json={"content": "# Вторая\n\nЕщё о кэше."})
    response = client.get(f"{URL}/search", params=params)
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["total"] == 2

    client.delete(f"{URL}/documents/{created['id']}")
    response = client.get(f"{URL}/search", params=params)
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["total"] == 1


def test_s5_two_clients_edit_one_document(client):
    """Одновременная правка: победитель один, второй получает 409 и не теряет данные."""
    created = client.post(f"{URL}/documents", json={"content": "# Заметка\n\nТекст."}).json()
    url = f"{URL}/documents/{created['id']}"

    def edit(number):
        body = {"content": f"# Заметка\n\nПравка {number}.", "expected_version": 1}
        return client.put(url, json=body).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = sorted(pool.map(edit, range(6)))

    assert codes == [200, 409, 409, 409, 409, 409]
    document = client.get(url).json()
    assert document["version"] == 2
    assert [v["version"] for v in client.get(f"{url}/versions").json()] == [2, 1]
    assert search_titles(client, document["content"].split()[-2]) == ["Заметка"]


def test_s6_service_restart_keeps_data(client_for, repository, users):
    """После перезапуска индекс строится из базы, поиск и версии на месте."""
    first = client_for(repository, users=users)
    created = first.post(f"{URL}/documents", json={"content": "# Заметка\n\nТекст о кэше."}).json()
    first.put(
        f"{URL}/documents/{created['id']}", json={"content": "# Заметка\n\nТекст об индексах."}
    )

    restarted = client_for(repository, users=users)
    assert search_titles(restarted, "индекс") == ["Заметка"]
    assert search_titles(restarted, "кэш") == []
    assert restarted.get(f"{URL}/documents/{created['id']}").json()["version"] == 2


def test_s7_redis_failure_does_not_stop_service(client_for, repository, users):
    """Redis недоступен: поиск и правка работают, состояние показывает деградацию."""
    client = client_for(repository, RedisCache(BrokenRedis()), users=users)
    created = client.post(f"{URL}/documents", json={"content": "# Заметка\n\nТекст о кэше."})
    assert created.status_code == 201
    response = client.get(f"{URL}/search", params={"q": "кэш"})
    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["total"] == 1
    assert client.get(f"{URL}/health").json() == {
        "status": "degraded",
        "database": "ok",
        "cache": "unavailable",
    }


def test_s8_redis_cache_works_through_api(client_for, repository, users):
    """Весь путь через клиент Redis: кэширование и сброс поколения."""
    server = fakeredis.FakeRedis(decode_responses=True)
    client = client_for(repository, RedisCache(server), users=users)
    client.post(f"{URL}/documents", json={"content": "# Заметка\n\nТекст о кэше."})
    params = {"q": "кэш"}
    assert client.get(f"{URL}/search", params=params).headers["X-Cache"] == "MISS"
    assert client.get(f"{URL}/search", params=params).headers["X-Cache"] == "HIT"
    # Общее поколение растёт при запуске сервиса, поколение пользователя при создании документа.
    assert server.get("kb:search:generation") == "1"
    assert server.get(f"kb:search:user:{OWNER}:generation") == "1"
    assert len(server.keys(f"kb:search:1:{OWNER}:1:*")) == 1


def test_s9_database_failure_gives_clean_error(client_for):
    """База недоступна: чтение и состояние отвечают 503 в едином формате, без трассировки."""

    class DownRepository(InMemoryRepository):
        def _fail(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        get = ping = create = access_level = _fail

    client = client_for(DownRepository())
    for response in (
        client.get(f"{URL}/health"),
        client.get(f"{URL}/documents/00000000-0000-0000-0000-000000000000"),
        client.post(f"{URL}/documents", json={"content": "# Заметка\n\nТекст."}),
    ):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
