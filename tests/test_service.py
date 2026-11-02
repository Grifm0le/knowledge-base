"""Сервис: согласованность хранилища, индекса и кэша без внешних зависимостей."""

import pytest

from fakes import InMemoryRepository
from kb.cache import MemoryCache, SearchCache
from kb.errors import (
    DocumentNotFoundError,
    DocumentValidationError,
    DuplicateSourceError,
    VersionConflictError,
    VersionNotFoundError,
)
from kb.search import SearchEngine, SearchQuery
from kb.service import STORAGE_SCORER, KnowledgeBase, search_methods

NOTE = "---\ntitle: Заметка о кэше\ntags: [кэш]\n---\n# Заметка\n\nКэш ускоряет чтение данных.\n"
CHANGED = NOTE.replace("ускоряет чтение", "снижает нагрузку на базу")


@pytest.fixture
def repository():
    return InMemoryRepository()


@pytest.fixture
def service(repository):
    return KnowledgeBase(repository, SearchEngine(), SearchCache(MemoryCache(), 60))


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
    assert repository.counts() == (0, 0, 0)


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


def test_index_is_rebuilt_from_repository_on_start(repository, service):
    service.create_document(NOTE)
    restarted = KnowledgeBase(repository, SearchEngine(), SearchCache(MemoryCache(), 60))
    assert titles(restarted, "кэш") == []
    assert restarted.load_index() == 1
    assert titles(restarted, "кэш") == ["Заметка о кэше"]


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

    def lost_race(draft):
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
