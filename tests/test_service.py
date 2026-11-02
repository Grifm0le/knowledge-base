"""Сервис: согласованность хранилища, индекса и кэша без внешних зависимостей."""

from datetime import datetime, timezone

import pytest

from fakes import InMemoryRepository, InMemoryUserRepository
from kb.cache import MemoryCache, SearchCache
from kb.errors import (
    AccessDeniedError,
    DocumentNotFoundError,
    DocumentValidationError,
    DuplicateSourceError,
    InvalidQueryError,
    TooManyRequestsError,
    UserNotFoundError,
    VersionConflictError,
    VersionNotFoundError,
)
from kb.ratelimit import RateLimiter
from kb.search import SearchQuery
from kb.service import STORAGE_SCORER, KnowledgeBase, search_methods

NOTE = "---\ntitle: Заметка о кэше\ntags: [кэш]\n---\n# Заметка\n\nКэш ускоряет чтение данных.\n"
CHANGED = NOTE.replace("ускоряет чтение", "снижает нагрузку на базу")


USER = "пользователь-1"
OTHER_EMAIL = "other@example.org"


def build_base(repository, users):
    limiter = RateLimiter(MemoryCache())
    return KnowledgeBase(repository, users, SearchCache(MemoryCache(), 60), limiter)


@pytest.fixture
def users():
    return InMemoryUserRepository()


@pytest.fixture
def repository(users):
    return InMemoryRepository(users)


@pytest.fixture
def base(repository, users):
    return build_base(repository, users)


@pytest.fixture
def service(base):
    return base.workspace(USER)


@pytest.fixture
def other_user(users):
    now = datetime.now(timezone.utc)
    user = users.create(OTHER_EMAIL, "!", now)
    users.mark_email_verified(user.id, now)
    return user


@pytest.fixture
def other(base, other_user):
    return base.workspace(other_user.id)


def titles(service, text, **kwargs):
    outcome = service.search(SearchQuery(text, **kwargs))
    return [hit["title"] for hit in outcome.payload["hits"]]


def test_created_document_is_found(service):
    stored = service.create_document(NOTE)
    assert stored.version == 1
    assert stored.title == "Заметка о кэше"
    assert titles(service, "кэш") == ["Заметка о кэше"]


def test_invalid_document_is_not_saved(service, repository):
    with pytest.raises(DocumentValidationError) as error:
        service.create_document("   ")
    assert error.value.code == "empty_document"
    assert repository.counts(USER) == (0, 0, 0)


def test_too_large_document_is_rejected(service):
    with pytest.raises(DocumentValidationError) as error:
        service.create_document("x" * (5 * 1024 * 1024 + 1))
    assert error.value.code == "document_too_large"


def test_update_creates_version_and_replaces_index(service):
    stored = service.create_document(NOTE)
    updated = service.update_document(stored.id, CHANGED)
    assert updated.version == 2
    assert titles(service, "нагрузку") == ["Заметка о кэше"]
    assert titles(service, "чтение") == []


def test_same_text_does_not_create_version(service):
    stored = service.create_document(NOTE)
    assert service.update_document(stored.id, NOTE).version == 1
    assert service.update_document(stored.id, NOTE.replace("\n", "\r\n")).version == 1
    assert len(service.list_versions(stored.id)) == 1


def test_outdated_version_is_rejected(service):
    stored = service.create_document(NOTE)
    service.update_document(stored.id, CHANGED, expected_version=1)
    with pytest.raises(VersionConflictError):
        service.update_document(stored.id, NOTE, expected_version=1)


def test_restore_returns_old_text_as_new_version(service):
    stored = service.create_document(NOTE)
    service.update_document(stored.id, CHANGED)
    restored = service.restore_version(stored.id, 1)
    assert restored.version == 3
    assert restored.content == NOTE
    assert [v.version for v in service.list_versions(stored.id)] == [3, 2, 1]
    assert titles(service, "чтение") == ["Заметка о кэше"]


def test_diff_shows_changed_lines(service):
    stored = service.create_document(NOTE)
    service.update_document(stored.id, CHANGED)
    diff = service.diff_versions(stored.id, 1, 2)
    assert "-Кэш ускоряет чтение данных." in diff
    assert "+Кэш снижает нагрузку на базу данных." in diff


def test_unknown_version_is_an_error(service):
    stored = service.create_document(NOTE)
    with pytest.raises(VersionNotFoundError):
        service.get_version(stored.id, 7)


def test_deleted_document_disappears_everywhere(service):
    stored = service.create_document(NOTE)
    service.delete_document(stored.id)
    assert titles(service, "кэш") == []
    with pytest.raises(DocumentNotFoundError):
        service.get_document(stored.id)


def test_deleting_unknown_document_is_an_error(service):
    with pytest.raises(DocumentNotFoundError):
        service.delete_document("нет")


def test_second_search_comes_from_cache(service):
    service.create_document(NOTE)
    first = service.search(SearchQuery("кэш"))
    second = service.search(SearchQuery("кэш"))
    assert (first.cache_hit, second.cache_hit) == (False, True)
    assert first.payload == second.payload


@pytest.mark.parametrize("change", ["update", "delete", "create"])
def test_any_change_resets_cache(service, change):
    stored = service.create_document(NOTE)
    service.search(SearchQuery("кэш"))
    if change == "update":
        service.update_document(stored.id, CHANGED)
    elif change == "delete":
        service.delete_document(stored.id)
    else:
        service.create_document(CHANGED)
    assert service.search(SearchQuery("кэш")).cache_hit is False


def test_unchanged_update_keeps_cache(service):
    stored = service.create_document(NOTE)
    service.search(SearchQuery("кэш"))
    service.update_document(stored.id, NOTE)
    assert service.search(SearchQuery("кэш")).cache_hit is True


def test_different_filters_are_cached_separately(service):
    service.create_document(NOTE)
    service.search(SearchQuery("кэш"))
    assert service.search(SearchQuery("кэш", limit=5)).cache_hit is False


def test_index_is_rebuilt_from_repository_on_start(repository, users, service):
    service.create_document(NOTE)
    restarted = build_base(repository, users)
    assert titles(restarted.workspace(USER), "кэш") == []
    assert restarted.load_index() == 1
    assert titles(restarted.workspace(USER), "кэш") == ["Заметка о кэше"]


def test_import_reports_each_file(service):
    report = service.import_files(
        [
            ("first.md", NOTE.encode()),
            ("empty.md", b"   "),
            ("cp1251.md", "# Кодировка".encode("cp1251")),
            ("notes.txt", b"text"),
        ]
    )
    assert report.created == ["first.md"]
    assert {f.name: f.code for f in report.failed} == {
        "empty.md": "empty_document",
        "cp1251.md": "invalid_encoding",
        "notes.txt": "unsupported_file_type",
    }


def test_repeated_import_updates_only_changed_files(service):
    service.import_files([("a.md", NOTE.encode()), ("b.md", NOTE.encode())])
    report = service.import_files([("a.md", NOTE.encode()), ("b.md", CHANGED.encode())])
    assert report.unchanged == ["a.md"]
    assert report.updated == ["b.md"]
    assert report.created == []


def test_duplicate_source_in_import_is_reported_for_this_file(service, repository, monkeypatch):
    """Другой запрос успел создать документ с этим именем между проверкой и сохранением."""

    def lost_race(owner_id, draft):
        raise DuplicateSourceError(draft.source_name)

    monkeypatch.setattr(repository, "create", lost_race)
    report = service.import_files([("a.md", NOTE.encode())])
    assert report.failed[0].code == "duplicate_source"


def test_title_falls_back_to_file_name(service):
    service.import_files([("план-работ.md", "Текст без заголовка.".encode())])
    assert titles(service, "текст") == ["план-работ"]


def test_storage_scorer_uses_repository_scores_and_engine_snippets(service):
    service.create_document(NOTE)
    outcome = service.search(SearchQuery("кэш", scorer=STORAGE_SCORER))
    hit = outcome.payload["hits"][0]
    assert hit["title"] == "Заметка о кэше"
    assert "<mark>Кэш</mark>" in hit["snippet"]


def test_storage_scorer_without_text_lists_documents(service):
    service.create_document(NOTE)
    assert titles(service, "", scorer=STORAGE_SCORER) == ["Заметка о кэше"]


def test_methods_list_includes_storage_search():
    assert search_methods() == ["bm25", "hybrid", "pgfts", "tfidf"]


def test_stats_reflect_repository_index_and_cache(service):
    stored = service.create_document(NOTE)
    service.update_document(stored.id, CHANGED)
    service.search(SearchQuery("кэш"))
    service.search(SearchQuery("кэш"))
    stats = service.stats()
    assert (stats.documents, stats.versions, stats.indexed_documents) == (1, 2, 1)
    assert (stats.cache_hits, stats.cache_misses) == (1, 1)
    assert stats.content_bytes > 0


def test_other_user_sees_nothing(service, other):
    service.create_document(NOTE)
    assert titles(other, "кэш") == []
    assert other.list_documents(10, 0) == ([], 0)
    assert other.stats().documents == 0
    assert titles(other, "кэш", scorer=STORAGE_SCORER) == []


@pytest.mark.parametrize(
    "action",
    [
        lambda s, i: s.get_document(i),
        lambda s, i: s.update_document(i, CHANGED),
        lambda s, i: s.delete_document(i),
        lambda s, i: s.list_versions(i),
        lambda s, i: s.get_version(i, 1),
        lambda s, i: s.restore_version(i, 1),
        lambda s, i: s.diff_versions(i, 1, 1),
        lambda s, i: s.preview(i),
        lambda s, i: s.list_shares(i),
        lambda s, i: s.share(i, "x@example.org", "read"),
    ],
)
def test_foreign_document_looks_missing(service, other, action):
    stored = service.create_document(NOTE)
    with pytest.raises(DocumentNotFoundError):
        action(other, stored.id)


def test_same_file_name_is_allowed_for_different_users(service, other):
    service.import_files([("a.md", NOTE.encode())])
    assert other.import_files([("a.md", NOTE.encode())]).created == ["a.md"]


def test_changes_of_one_user_keep_cache_of_another(service, other):
    other.create_document(NOTE)
    other.search(SearchQuery("кэш"))
    service.create_document(NOTE)
    assert other.search(SearchQuery("кэш")).cache_hit is True


def test_shared_document_is_found_and_read(service, other):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL, "read")
    assert titles(other, "кэш") == ["Заметка о кэше"]
    assert other.get_document(stored.id).access == "read"
    assert [d.access for d in other.list_documents(10, 0)[0]] == ["read"]


@pytest.mark.parametrize(
    "action",
    [
        lambda s, i: s.update_document(i, CHANGED),
        lambda s, i: s.restore_version(i, 1),
        lambda s, i: s.delete_document(i),
        lambda s, i: s.list_shares(i),
        lambda s, i: s.share(i, "x@example.org", "write"),
    ],
)
def test_read_access_does_not_allow_changes(service, other, action):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL, "read")
    with pytest.raises(AccessDeniedError):
        action(other, stored.id)


def test_write_access_updates_index_of_everyone(service, other):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL, "write")
    assert other.update_document(stored.id, CHANGED).access == "write"
    assert titles(service, "нагрузку") == ["Заметка о кэше"]
    assert titles(other, "нагрузку") == ["Заметка о кэше"]


def test_write_access_does_not_allow_delete(service, other):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL, "write")
    with pytest.raises(AccessDeniedError):
        other.delete_document(stored.id)


def test_change_by_owner_resets_cache_of_invited_user(service, other):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL, "read")
    other.search(SearchQuery("кэш"))
    service.update_document(stored.id, CHANGED)
    assert other.search(SearchQuery("кэш")).cache_hit is False


def test_revoked_access_removes_document_from_search(service, other, other_user):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL, "read")
    other.search(SearchQuery("кэш"))
    service.unshare(stored.id, other_user.id)
    assert titles(other, "кэш") == []
    with pytest.raises(DocumentNotFoundError):
        other.get_document(stored.id)


def test_user_can_give_up_shared_document(service, other, other_user):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL, "read")
    other.unshare(stored.id, other_user.id)
    assert titles(other, "кэш") == []


def test_unshare_without_share_is_an_error(service, other_user):
    stored = service.create_document(NOTE)
    with pytest.raises(UserNotFoundError):
        service.unshare(stored.id, other_user.id)


def test_deleting_shared_document_removes_it_from_all_indexes(service, other):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL, "read")
    service.delete_document(stored.id)
    assert titles(other, "кэш") == []


def test_shares_are_restored_after_restart(repository, users, service, other_user):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL, "read")
    restarted = build_base(repository, users)
    restarted.load_index()
    assert titles(restarted.workspace(other_user.id), "кэш") == ["Заметка о кэше"]


def test_share_lists_invited_users(service, other_user):
    stored = service.create_document(NOTE)
    service.share(stored.id, OTHER_EMAIL.upper(), "write")
    shares = service.list_shares(stored.id)
    assert [(s.user_id, s.email, s.permission) for s in shares] == [
        (other_user.id, OTHER_EMAIL, "write")
    ]


def test_share_with_unknown_or_unverified_user_is_an_error(service, users):
    stored = service.create_document(NOTE)
    users.create("new@example.org", "!", datetime.now(timezone.utc))
    for email in ("nobody@example.org", "new@example.org"):
        with pytest.raises(UserNotFoundError):
            service.share(stored.id, email, "read")


def test_share_with_self_is_an_error(users, base):
    now = datetime.now(timezone.utc)
    owner = users.create("self@example.org", "!", now)
    users.mark_email_verified(owner.id, now)
    workspace = base.workspace(owner.id)
    stored = workspace.create_document(NOTE)
    with pytest.raises(InvalidQueryError):
        workspace.share(stored.id, "self@example.org", "read")


def test_address_guessing_through_share_is_limited(service):
    stored = service.create_document(NOTE)
    for number in range(30):
        with pytest.raises(UserNotFoundError):
            service.share(stored.id, f"guess{number}@example.org", "read")
    with pytest.raises(TooManyRequestsError):
        service.share(stored.id, "guess@example.org", "read")


def test_preview_escapes_html_and_drops_front_matter(service):
    stored = service.create_document(
        "---\ntitle: Опасная\n---\n# Заголовок\n\n<script>alert(1)</script>\n\n"
        "[ссылка](javascript:alert(1))\n\n**важно**\n"
    )
    html = service.preview(stored.id)
    assert "<h1>Заголовок</h1>" in html
    assert "<strong>важно</strong>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'href="javascript' not in html
    assert "title: Опасная" not in html


def test_stats_count_tags_and_categories(service):
    service.create_document("---\ntitle: А\ntags: [кэш, redis]\ncategory: Базы\n---\nТекст.")
    service.create_document("---\ntitle: Б\ntags: [кэш]\n---\nТекст.")
    stats = service.stats()
    assert [(t.name, t.count) for t in stats.top_tags] == [("кэш", 2), ("redis", 1)]
    assert [(c.name, c.count) for c in stats.categories] == [("Базы", 1)]


WEIGHTED_NOTES = {
    "В заголовке": "---\ntitle: Кэширование\n---\nПростой текст заметки.",
    "В тексте": "---\ntitle: Заметка\n---\nТекст про кэширование данных в памяти.",
}


@pytest.fixture
def weighted(service):
    for content in WEIGHTED_NOTES.values():
        service.create_document(content)
    return service


@pytest.mark.parametrize("scorer", ["tfidf", "bm25", "hybrid"])
def test_field_weights_change_order(weighted, scorer):
    by_title = titles(
        weighted, "кэширование", scorer=scorer, field_weights=(("body", 0.1), ("title", 10.0))
    )
    by_body = titles(
        weighted, "кэширование", scorer=scorer, field_weights=(("body", 10.0), ("title", 0.1))
    )
    assert by_title == ["Кэширование", "Заметка"]
    assert by_body == ["Заметка", "Кэширование"]


def test_zero_field_weight_excludes_field(weighted):
    found = titles(weighted, "кэширование", field_weights=(("body", 0.0), ("title", 1.0)))
    assert found == ["Кэширование"]


def test_method_weights_give_only_one_method(weighted):
    only_bm25 = weighted.search(
        SearchQuery("кэширование", scorer="hybrid", method_weights=(("bm25", 1.0), ("tfidf", 0.0)))
    )
    assert only_bm25.payload["total"] == 2


@pytest.mark.parametrize(
    "params",
    [
        {"field_weights": (("title", 0.0), ("body", 0.0))},
        {"field_weights": (("title", -1.0),)},
        {"field_weights": (("нет", 1.0),)},
        {"method_weights": (("bm25", 0.0), ("tfidf", 0.0))},
        {"method_weights": (("bm25", -1.0),)},
        {"method_weights": (("pgfts", 1.0),)},
    ],
)
def test_bad_weights_are_rejected(service, params):
    with pytest.raises(InvalidQueryError):
        service.search(SearchQuery("кэш", **params))


def test_custom_weights_are_cached_separately(weighted):
    weighted.search(SearchQuery("кэширование"))
    custom = weighted.search(SearchQuery("кэширование", field_weights=(("title", 10.0),)))
    assert custom.cache_hit is False
