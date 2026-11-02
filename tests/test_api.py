"""REST API на настоящем PostgreSQL: коды ответов, форматы и обработка ошибок."""

import pytest

pytestmark = pytest.mark.postgres

NOTE = """---
title: Кэширование
tags: [Redis, кэш]
category: Базы данных
author: Автор
created: 2026-10-11
---
# Кэширование

Кэш хранит результат тяжёлой операции.
"""
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


def create(client, content=NOTE, **extra):
    response = client.post("/api/v1/documents", json={"content": content, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def assert_error(response, status_code, code):
    assert response.status_code == status_code, response.text
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"]


def test_create_returns_document(client):
    body = create(client, source_name="cache.md")
    assert body["version"] == 1
    assert body["title"] == "Кэширование"
    assert body["tags"] == ["redis", "кэш"]
    assert body["created"] == "2026-10-11"
    assert body["content"] == NOTE
    assert body["source_name"] == "cache.md"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("   ", "empty_document"),
        ("---\ntags: [\n---\nТекст", "invalid_front_matter"),
        ("---\ncreated: вчера\n---\nТекст", "invalid_date"),
        ("---\ntags: {a: 1}\n---\nТекст", "invalid_tags"),
    ],
)
def test_invalid_document_gives_422(client, content, code):
    assert_error(client.post("/api/v1/documents", json={"content": content}), 422, code)


def test_create_without_content_gives_invalid_request(client):
    response = client.post("/api/v1/documents", json={})
    assert_error(response, 422, "invalid_request")
    assert response.json()["error"]["details"][0]["field"] == "body.content"


def test_same_source_name_twice_is_a_clean_error(client):
    create(client, source_name="cache.md")
    response = client.post(
        "/api/v1/documents", json={"content": NOTE + "\nЕщё.", "source_name": "cache.md"}
    )
    assert_error(response, 409, "duplicate_source")


def test_null_character_in_document_is_rejected(client):
    response = client.post("/api/v1/documents", json={"content": "# Заметка\n\nТекст\x00с нулём"})
    assert_error(response, 422, "invalid_characters")


def test_null_character_in_query_does_not_break_search(client):
    create(client)
    for scorer in ("bm25", "pgfts"):
        response = client.get("/api/v1/search", params={"q": "кэш\x00", "scorer": scorer})
        assert response.status_code == 200, response.text


def test_get_document(client):
    created = create(client)
    response = client.get(f"/api/v1/documents/{created['id']}")
    assert response.status_code == 200
    assert response.json()["content"] == NOTE


def test_get_unknown_document_gives_404(client):
    assert_error(client.get(f"/api/v1/documents/{UNKNOWN_ID}"), 404, "document_not_found")


def test_bad_identifier_gives_invalid_request(client):
    assert_error(client.get("/api/v1/documents/abc"), 422, "invalid_request")


def test_list_is_paged_without_content(client):
    for number in range(3):
        create(client, f"# Заметка {number}\n\nТекст {number}.")
    body = client.get("/api/v1/documents", params={"limit": 2, "offset": 1}).json()
    assert body["total"] == 3
    assert (body["limit"], body["offset"]) == (2, 1)
    assert len(body["items"]) == 2
    assert "content" not in body["items"][0]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_bad_paging_gives_invalid_request(client, params):
    assert_error(client.get("/api/v1/documents", params=params), 422, "invalid_request")


def test_update_creates_version(client):
    created = create(client)
    response = client.put(
        f"/api/v1/documents/{created['id']}",
        json={"content": NOTE + "\nПравка.", "expected_version": 1},
    )
    assert response.status_code == 200
    assert response.json()["version"] == 2


def test_update_with_outdated_version_gives_409(client):
    created = create(client)
    url = f"/api/v1/documents/{created['id']}"
    client.put(url, json={"content": NOTE + "\nПравка."})
    response = client.put(url, json={"content": NOTE + "\nДругая.", "expected_version": 1})
    assert_error(response, 409, "version_conflict")


def test_update_unknown_document_gives_404(client):
    response = client.put(f"/api/v1/documents/{UNKNOWN_ID}", json={"content": NOTE})
    assert_error(response, 404, "document_not_found")


def test_delete(client):
    created = create(client)
    url = f"/api/v1/documents/{created['id']}"
    assert client.delete(url).status_code == 204
    assert_error(client.get(url), 404, "document_not_found")
    assert_error(client.delete(url), 404, "document_not_found")


def test_versions_diff_and_restore(client):
    created = create(client)
    url = f"/api/v1/documents/{created['id']}"
    client.put(url, json={"content": NOTE.replace("тяжёлой", "дорогой")})

    versions = client.get(f"{url}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert "content" not in versions[0]
    assert client.get(f"{url}/versions/1").json()["content"] == NOTE

    diff = client.get(f"{url}/diff", params={"base": 1, "target": 2}).json()["diff"]
    assert "-Кэш хранит результат тяжёлой операции." in diff
    assert "+Кэш хранит результат дорогой операции." in diff

    restored = client.post(f"{url}/versions/1/restore").json()
    assert restored["version"] == 3
    assert restored["content"] == NOTE


def test_unknown_version_gives_404(client):
    created = create(client)
    url = f"/api/v1/documents/{created['id']}/versions/9"
    assert_error(client.get(url), 404, "version_not_found")
    assert_error(client.post(f"{url}/restore"), 404, "version_not_found")


@pytest.mark.parametrize("scorer", ["bm25", "tfidf", "hybrid", "pgfts"])
def test_every_scorer_finds_document(client, scorer):
    create(client)
    create(client, "# Другая заметка\n\nТекст про индексы.")
    body = client.get("/api/v1/search", params={"q": "кэшу", "scorer": scorer}).json()
    assert body["scorer"] == scorer
    assert body["total"] == 1
    assert body["hits"][0]["title"] == "Кэширование"
    assert body["hits"][0]["score"] > 0
    assert "<mark>" in body["hits"][0]["snippet"]


def test_filters_and_sort(client):
    create(client)
    create(client, "---\ntitle: Анализ\ntags: [кэш]\ncreated: 2026-01-01\n---\nКэш и анализ.")
    url = "/api/v1/search"
    assert client.get(url, params={"q": "кэш", "tags": ["Redis"]}).json()["total"] == 1
    assert client.get(url, params={"q": "кэш", "category": "базы данных"}).json()["total"] == 1
    assert client.get(url, params={"q": "кэш", "author": "автор"}).json()["total"] == 1
    assert client.get(url, params={"q": "кэш", "created_from": "2026-06-01"}).json()["total"] == 1
    assert client.get(url, params={"q": "кэш", "created_to": "2026-06-01"}).json()["total"] == 1
    by_title = client.get(url, params={"q": "кэш", "sort": "title"}).json()["hits"]
    assert [hit["title"] for hit in by_title] == ["Анализ", "Кэширование"]
    by_date = client.get(url, params={"q": "кэш", "sort": "created"}).json()["hits"]
    assert [hit["title"] for hit in by_date] == ["Кэширование", "Анализ"]


@pytest.mark.parametrize(
    ("params", "status_code", "code"),
    [
        ({"q": "x", "scorer": "нет"}, 400, "unknown_scorer"),
        ({"q": "x", "sort": "нет"}, 400, "invalid_query"),
        ({"q": "x", "limit": 0}, 422, "invalid_request"),
        ({"q": "x", "created_from": "вчера"}, 422, "invalid_request"),
    ],
)
def test_bad_search_parameters(client, params, status_code, code):
    assert_error(client.get("/api/v1/search", params=params), status_code, code)


def test_search_marks_cache_state_in_header(client):
    create(client)
    url = "/api/v1/search"
    assert client.get(url, params={"q": "кэш"}).headers["X-Cache"] == "MISS"
    assert client.get(url, params={"q": "кэш"}).headers["X-Cache"] == "HIT"


def test_html_in_document_is_escaped_in_snippet(client):
    create(client, "# Заметка\n\nПоиск <script>alert(1)</script> в тексте.")
    hit = client.get("/api/v1/search", params={"q": "поиск"}).json()["hits"][0]
    assert "<script>" not in hit["snippet"]
    assert "<mark>Поиск</mark>" in hit["snippet"]


def test_import_files(client):
    files = [
        ("files", ("good.md", NOTE.encode(), "text/markdown")),
        ("files", ("bad.md", "текст".encode("cp1251"), "text/markdown")),
    ]
    body = client.post("/api/v1/documents/import", files=files).json()
    assert body["created"] == ["good.md"]
    assert body["failed"][0]["name"] == "bad.md"
    assert body["failed"][0]["code"] == "invalid_encoding"


def test_import_without_files_gives_invalid_request(client):
    assert_error(client.post("/api/v1/documents/import"), 422, "invalid_request")


def test_stats(client):
    create(client)
    body = client.get("/api/v1/stats").json()
    assert (body["documents"], body["versions"], body["indexed_documents"]) == (1, 1, 1)
    assert body["indexed_terms"] > 0


def test_health(client):
    assert client.get("/api/v1/health").json() == {
        "status": "ok",
        "database": "ok",
        "cache": "ok",
    }


def test_responses_carry_request_id(client):
    response = client.get("/api/v1/health", headers={"X-Request-ID": "проверка-1".encode().hex()})
    assert response.headers["X-Request-ID"] == "проверка-1".encode().hex()
    assert client.get("/api/v1/health").headers["X-Request-ID"]


def test_openapi_describes_all_methods(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Личная база знаний"
    assert set(schema["paths"]) == {
        "/api/v1/documents",
        "/api/v1/documents/import",
        "/api/v1/documents/{doc_id}",
        "/api/v1/documents/{doc_id}/versions",
        "/api/v1/documents/{doc_id}/versions/{version}",
        "/api/v1/documents/{doc_id}/versions/{version}/restore",
        "/api/v1/documents/{doc_id}/diff",
        "/api/v1/documents/{doc_id}/preview",
        "/api/v1/documents/{doc_id}/shares",
        "/api/v1/documents/{doc_id}/shares/{user_id}",
        "/api/v1/auth/register",
        "/api/v1/auth/verify-email",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/me/delete",
        "/api/v1/auth/password/change",
        "/api/v1/auth/password/forgot",
        "/api/v1/auth/password/reset",
        "/api/v1/search",
        "/api/v1/stats",
        "/api/v1/health",
    }
    assert "ErrorOut" in schema["components"]["schemas"]
