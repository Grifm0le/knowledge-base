"""Изоляция данных и управление доступом через API на настоящем PostgreSQL."""

import pytest
from sqlalchemy import text

from conftest import PASSWORD, bearer, verified_user

pytestmark = pytest.mark.postgres

URL = "/api/v1"
OTHER_EMAIL = "other@example.org"
SECRET_NOTE = "---\ntitle: Секретная заметка\ntags: [личное]\n---\n# Секрет\n\nПароль от сейфа.\n"
CHANGED_NOTE = SECRET_NOTE.replace("Пароль от сейфа", "Код от сейфа")


@pytest.fixture
def other(client, users):
    """Заголовок второго пользователя."""
    return bearer(client, verified_user(users, OTHER_EMAIL))


@pytest.fixture
def doc_url(client):
    created = client.post(f"{URL}/documents", json={"content": SECRET_NOTE})
    assert created.status_code == 201
    return f"{URL}/documents/{created.json()['id']}"


def share(client, doc_url, permission="read", email=OTHER_EMAIL):
    return client.put(f"{doc_url}/shares", json={"email": email, "permission": permission})


def search(client, headers=None, **params):
    return client.get(f"{URL}/search", params=params, headers=headers).json()


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("get", "", None),
        ("put", "", {"content": CHANGED_NOTE}),
        ("delete", "", None),
        ("get", "/preview", None),
        ("get", "/versions", None),
        ("get", "/versions/1", None),
        ("post", "/versions/1/restore", None),
        ("get", "/diff?base=1&target=1", None),
        ("get", "/shares", None),
        ("put", "/shares", {"email": OTHER_EMAIL, "permission": "write"}),
    ],
)
def test_foreign_document_answers_like_missing_one(client, other, doc_url, method, suffix, body):
    kwargs = {"json": body} if body else {}
    foreign = getattr(client, method)(f"{doc_url}{suffix}", headers=other, **kwargs)
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "document_not_found"
    assert client.get(doc_url).json()["version"] == 1


def test_foreign_documents_are_not_listed_found_or_counted(client, other, doc_url):
    assert client.get(f"{URL}/documents", headers=other).json()["total"] == 0
    assert client.get(f"{URL}/stats", headers=other).json()["documents"] == 0
    for scorer in ("bm25", "tfidf", "hybrid", "pgfts"):
        assert search(client, other, q="сейф", scorer=scorer)["total"] == 0
        assert search(client, q="сейф", scorer=scorer)["total"] == 1


def test_same_query_of_two_users_is_cached_separately(client, other, doc_url):
    assert search(client, q="сейф")["total"] == 1
    response = client.get(f"{URL}/search", params={"q": "сейф"}, headers=other)
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["total"] == 0


def test_same_file_name_for_two_users(client, other):
    files = [("files", ("note.md", SECRET_NOTE.encode(), "text/markdown"))]
    assert client.post(f"{URL}/documents/import", files=files).json()["created"] == ["note.md"]
    response = client.post(f"{URL}/documents/import", files=files, headers=other)
    assert response.json()["created"] == ["note.md"]


def test_read_access_opens_document_but_not_changes(client, other, doc_url):
    shared = share(client, doc_url)
    assert shared.status_code == 200
    assert shared.json()["email"] == OTHER_EMAIL

    document = client.get(doc_url, headers=other)
    assert document.status_code == 200
    assert document.json()["access"] == "read"
    assert client.get(f"{doc_url}/preview", headers=other).status_code == 200
    assert search(client, other, q="сейф")["total"] == 1
    assert search(client, other, q="сейф", scorer="pgfts")["total"] == 1

    denied = client.put(doc_url, json={"content": CHANGED_NOTE}, headers=other)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "access_denied"
    assert client.delete(doc_url, headers=other).status_code == 403
    assert client.post(f"{doc_url}/versions/1/restore", headers=other).status_code == 403
    assert client.get(f"{doc_url}/shares", headers=other).status_code == 403


def test_write_access_saves_versions_but_does_not_delete(client, other, doc_url):
    share(client, doc_url, "write")
    updated = client.put(doc_url, json={"content": CHANGED_NOTE}, headers=other)
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert search(client, q="код")["total"] == 1
    assert client.delete(doc_url, headers=other).status_code == 403
    assert (
        client.put(
            f"{doc_url}/shares",
            json={"email": "x@example.org", "permission": "read"},
            headers=other,
        ).status_code
        == 403
    )


def test_changing_permission_replaces_old_one(client, other, doc_url):
    share(client, doc_url, "write")
    share(client, doc_url, "read")
    assert [s["permission"] for s in client.get(f"{doc_url}/shares").json()] == ["read"]
    assert client.put(doc_url, json={"content": CHANGED_NOTE}, headers=other).status_code == 403


def test_revoked_access_closes_document(client, other, doc_url, users):
    share(client, doc_url)
    search(client, other, q="сейф")
    target = users.find_by_email(OTHER_EMAIL).id
    assert client.delete(f"{doc_url}/shares/{target}").status_code == 204
    assert client.get(doc_url, headers=other).status_code == 404
    assert search(client, other, q="сейф")["total"] == 0
    assert client.get(f"{URL}/documents", headers=other).json()["total"] == 0


def test_share_with_unknown_address_is_an_error(client, doc_url):
    response = share(client, doc_url, email="nobody@example.org")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


def test_share_with_unknown_level_is_rejected(client, doc_url):
    response = share(client, doc_url, "admin")
    assert response.status_code == 422


def test_shares_survive_restart(client_for, repository, users, client, other, doc_url):
    share(client, doc_url)
    restarted = client_for(repository, users=users)
    assert search(restarted, other, q="сейф")["total"] == 1


def test_list_shows_access_level(client, other, doc_url):
    share(client, doc_url, "write")
    items = client.get(f"{URL}/documents", headers=other).json()["items"]
    assert [item["access"] for item in items] == ["write"]
    assert client.get(f"{URL}/documents").json()["items"][0]["access"] == "owner"


def test_deleted_account_takes_documents_from_everyone(client, other, doc_url, pg_engine):
    share(client, doc_url)
    assert search(client, other, q="сейф")["total"] == 1
    response = client.post(f"{URL}/auth/me/delete", json={"password": PASSWORD})
    assert response.status_code == 204

    assert search(client, other, q="сейф")["total"] == 0
    with pg_engine.connect() as connection:
        for table in ("documents", "document_versions", "document_shares"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608


def test_sql_in_search_text_is_only_text(client, doc_url):
    attack = "'; DROP TABLE documents; --"
    for scorer in ("bm25", "pgfts"):
        assert (
            client.get(f"{URL}/search", params={"q": attack, "scorer": scorer}).status_code == 200
        )
    assert client.get(doc_url).status_code == 200


def test_preview_is_safe_html(client):
    created = client.post(
        f"{URL}/documents",
        json={"content": "# Заголовок\n\n<img src=x onerror=alert(1)>\n\n**важно**\n"},
    ).json()
    html = client.get(f"{URL}/documents/{created['id']}/preview").json()["html"]
    assert "<strong>важно</strong>" in html
    assert "<img" not in html


def test_snippet_escapes_html_from_document(client):
    client.post(f"{URL}/documents", json={"content": "# Т\n\nслово <script>alert(1)</script>"})
    snippet = search(client, q="слово")["hits"][0]["snippet"]
    assert "<script>" not in snippet
    assert "<mark>" in snippet


def test_security_headers_are_set(client):
    headers = client.get(f"{URL}/documents").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cache-Control"] == "no-store"
    assert "default-src 'self'" in headers["Content-Security-Policy"]


def test_docs_page_works_without_strict_policy(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers


WEIGHTED = {
    "title": "---\ntitle: Кэширование\n---\nПростой текст заметки.",
    "body": "---\ntitle: Заметка\n---\nТекст про кэширование данных в памяти.",
}


@pytest.fixture
def weighted(client):
    for content in WEIGHTED.values():
        assert client.post(f"{URL}/documents", json={"content": content}).status_code == 201
    return client


def titles(client, **params):
    return [hit["title"] for hit in search(client, q="кэширование", **params)["hits"]]


@pytest.mark.parametrize("scorer", ["tfidf", "bm25", "hybrid", "pgfts"])
def test_field_weights_change_order_through_api(weighted, scorer):
    assert titles(weighted, scorer=scorer, w_title=10, w_body=0.1) == ["Кэширование", "Заметка"]
    assert titles(weighted, scorer=scorer, w_title=0.1, w_body=10) == ["Заметка", "Кэширование"]


@pytest.mark.parametrize("scorer", ["bm25", "pgfts"])
def test_zero_weight_turns_field_off(weighted, scorer):
    assert titles(weighted, scorer=scorer, w_title=1, w_body=0) == ["Кэширование"]


def test_hybrid_weights_are_accepted(weighted):
    found = search(weighted, q="кэширование", scorer="hybrid", hybrid_bm25=1, hybrid_tfidf=0)
    assert found["total"] == 2


@pytest.mark.parametrize(
    "params",
    [{"w_title": -1}, {"w_title": 101}, {"w_body": "много"}, {"hybrid_bm25": -1}],
)
def test_weight_outside_range_is_rejected(client, params):
    response = client.get(f"{URL}/search", params={"q": "кэш", **params})
    assert response.status_code == 422


def test_all_zero_weights_are_rejected(client):
    params = {"q": "кэш", "w_title": 0, "w_headings": 0, "w_tags": 0, "w_body": 0, "w_code": 0}
    response = client.get(f"{URL}/search", params=params)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_query"


def test_stats_through_api_include_tags(client, doc_url):
    body = client.get(f"{URL}/stats").json()
    assert body["top_tags"] == [{"name": "личное", "count": 1}]
    assert body["categories"] == []
