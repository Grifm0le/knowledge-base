"""Хранилище на настоящем PostgreSQL: транзакции, версии, индексы и миграции."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from conftest import OWNER, alembic_config
from kb.errors import (
    DocumentNotFoundError,
    DuplicateSourceError,
    VersionConflictError,
    VersionNotFoundError,
)
from kb.parser import parse_document
from kb.storage.repository import DocumentDraft
from kb.storage.tables import Base

pytestmark = pytest.mark.postgres

NOTE = """---
title: Индексы в базе
tags: [PostgreSQL, Индексы]
category: Базы данных
author: Автор
created: 2026-10-05
---
# Индексы в базе

## Частичный индекс

Индексы ускоряют поиск строк.

```sql
EXPLAIN ANALYZE SELECT 1;
```
"""


def draft(content=NOTE, source_name=None):
    return DocumentDraft(content, parse_document(content), source_name)


def test_create_saves_metadata_and_first_version(repository):
    stored = repository.create(OWNER, draft())
    loaded = repository.get(stored.id)
    assert loaded.version == 1
    assert loaded.title == "Индексы в базе"
    assert loaded.tags == ("postgresql", "индексы")
    assert (loaded.category, loaded.author) == ("Базы данных", "Автор")
    assert loaded.created == date(2026, 10, 5)
    assert loaded.content == NOTE
    assert loaded.created_at.tzinfo is not None
    assert [v.version for v in repository.list_versions(stored.id)] == [1]


def test_update_adds_version_and_keeps_history(repository):
    stored = repository.create(OWNER, draft())
    changed = NOTE.replace("ускоряют", "замедляют запись, но ускоряют")
    result = repository.update(stored.id, draft(changed))
    assert result.changed is True
    assert result.document.version == 2
    assert repository.get(stored.id).content == changed
    assert repository.get_version(stored.id, 1).content == NOTE
    assert result.document.updated_at >= stored.updated_at


def test_same_text_does_not_add_version(repository):
    stored = repository.create(OWNER, draft())
    result = repository.update(stored.id, draft(NOTE.replace("\n", "\r\n")))
    assert result.changed is False
    assert result.document.version == 1


def test_expected_version_is_checked(repository):
    stored = repository.create(OWNER, draft())
    repository.update(stored.id, draft(NOTE + "\nЕщё."), expected_version=1)
    with pytest.raises(VersionConflictError):
        repository.update(stored.id, draft(NOTE + "\nИ ещё."), expected_version=1)
    assert repository.get(stored.id).version == 2


def test_concurrent_updates_get_different_versions(repository):
    stored = repository.create(OWNER, draft())

    def save(number):
        return repository.update(stored.id, draft(f"{NOTE}\nПравка {number}.")).document.version

    with ThreadPoolExecutor(max_workers=8) as pool:
        versions = list(pool.map(save, range(8)))

    assert sorted(versions) == list(range(2, 10))
    assert [v.version for v in repository.list_versions(stored.id)] == list(range(9, 0, -1))


def test_only_one_of_concurrent_updates_with_same_expected_version_wins(repository):
    stored = repository.create(OWNER, draft())

    def save(number):
        try:
            repository.update(stored.id, draft(f"{NOTE}\nПравка {number}."), expected_version=1)
        except VersionConflictError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(save, range(8)))

    assert outcomes.count(True) == 1
    assert repository.get(stored.id).version == 2


def test_delete_removes_versions_by_cascade(repository, pg_engine):
    stored = repository.create(OWNER, draft())
    repository.delete(stored.id)
    with pytest.raises(DocumentNotFoundError):
        repository.get(stored.id)
    with pg_engine.connect() as connection:
        left = connection.execute(text("SELECT count(*) FROM document_versions")).scalar()
    assert left == 0


@pytest.mark.parametrize("method", ["get", "delete", "list_versions"])
def test_unknown_document_is_an_error(repository, method):
    with pytest.raises(DocumentNotFoundError):
        getattr(repository, method)("00000000-0000-0000-0000-000000000000")


def test_unknown_version_is_an_error(repository):
    stored = repository.create(OWNER, draft())
    with pytest.raises(VersionNotFoundError):
        repository.get_version(stored.id, 2)


def test_find_by_source(repository):
    stored = repository.create(OWNER, draft(source_name="notes/index.md"))
    assert repository.find_by_source(OWNER, "notes/index.md").id == stored.id
    assert repository.find_by_source(OWNER, "другой.md") is None


def test_list_is_paged_and_fresh_documents_go_first(repository):
    ids = [repository.create(OWNER, draft(f"{NOTE}\nДокумент {n}.")).id for n in range(3)]
    repository.update(ids[0], draft(f"{NOTE}\nСвежая правка."))
    page, total = repository.list_documents(OWNER, limit=2, offset=0)
    assert total == 3
    assert len(page) == 2
    assert page[0].id == ids[0]


def test_iter_current_returns_last_versions(repository):
    stored = repository.create(OWNER, draft())
    repository.update(stored.id, draft(f"{NOTE}\nПоследняя версия."))
    assert [d.content for d in repository.iter_current()] == [f"{NOTE}\nПоследняя версия."]


@pytest.mark.parametrize(
    "query",
    ["индексов", "ускорять", "поиске", "частичного", "postgresql", "EXPLAIN"],
    ids=["title", "body", "body-form", "heading", "tag", "code"],
)
def test_fulltext_finds_word_forms_in_every_field(repository, query):
    stored = repository.create(OWNER, draft())
    assert stored.id in repository.fulltext_scores(OWNER, query)


def test_fulltext_finds_document_with_any_word(repository):
    stored = repository.create(OWNER, draft())
    assert stored.id in repository.fulltext_scores(OWNER, "индексы кошка")


@pytest.mark.parametrize("word", ["кэширование", "ускоряют", "индексы"])
def test_fulltext_does_not_stem_query_twice(repository, word):
    stored = repository.create(OWNER, draft(f"# Заметка\n\nСлово {word} в тексте."))
    assert stored.id in repository.fulltext_scores(OWNER, word)


def test_null_character_in_query_is_ignored(repository):
    stored = repository.create(OWNER, draft())
    assert stored.id in repository.fulltext_scores(OWNER, "индексы\x00")


def test_duplicate_source_name_is_a_domain_error_and_saves_nothing(repository):
    repository.create(OWNER, draft(source_name="index.md"))
    with pytest.raises(DuplicateSourceError):
        repository.create(OWNER, draft(NOTE + "\nЕщё.", source_name="index.md"))
    assert repository.counts(OWNER)[:2] == (1, 1)


def test_fulltext_gives_title_more_weight_than_code(repository):
    in_title = repository.create(OWNER, draft("---\ntitle: Кэширование\n---\nТекст заметки.")).id
    in_code = repository.create(OWNER, draft("# Заметка\n\nТекст.\n\n```\nкэширование\n```\n")).id
    scores = repository.fulltext_scores(OWNER, "кэширование")
    assert scores[in_title] > scores[in_code]


@pytest.mark.parametrize("query", ["и в на", "", "   "])
def test_fulltext_without_meaningful_words_finds_nothing(repository, query):
    repository.create(OWNER, draft())
    assert repository.fulltext_scores(OWNER, query) == {}


def test_fulltext_uses_gin_index(repository, pg_engine):
    repository.create(OWNER, draft())
    with pg_engine.begin() as connection:
        # На маленькой таблице планировщик выбирает полный просмотр: он там дешевле.
        # Здесь проверяется, что индекс вообще подходит к условию поиска.
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = (
            connection.execute(
                text(
                    "EXPLAIN SELECT id FROM documents WHERE search_vector @@ "
                    "replace(websearch_to_tsquery('russian', 'индексы поиска')::text, "
                    "' & ', ' | ')::tsquery"
                )
            )
            .scalars()
            .all()
        )
    assert "ix_documents_search_vector" in "\n".join(plan)


def test_counts(repository):
    stored = repository.create(OWNER, draft())
    repository.update(stored.id, draft(f"{NOTE}\nПравка."))
    documents, versions, size = repository.counts(OWNER)
    assert (documents, versions) == (1, 2)
    assert size >= 2 * len(NOTE.encode())


def test_ping(repository):
    repository.ping()


def test_models_match_migrations(pg_engine):
    with pg_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_migrations_go_down_and_up_keeping_data(database_url, pg_engine, repository):
    stored = repository.create(OWNER, draft())
    config = alembic_config(database_url)
    command.downgrade(config, "0002")
    assert "keywords" not in {c["name"] for c in inspect(pg_engine).get_columns("documents")}
    command.upgrade(config, "head")
    with pg_engine.connect() as connection:
        row = connection.execute(
            text("SELECT keywords, code FROM documents WHERE id = :id"), {"id": stored.id}
        ).one()
    assert "Частичный индекс" in row.keywords
    assert "EXPLAIN" in row.code
    # Документы, созданные до появления пользователей, принадлежат служебной записи.
    owner = repository.get(stored.id).owner_id
    assert stored.id in repository.fulltext_scores(owner, "частичного")
