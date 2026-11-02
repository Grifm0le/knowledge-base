from datetime import date

import pytest

from kb.errors import DocumentNotFoundError, InvalidQueryError, UnknownScorerError
from kb.parser import parse_document
from kb.search import SearchEngine, SearchFilters, SearchQuery

DOCS = {
    "pg": (
        "---\ntitle: Индексы PostgreSQL\ntags: [postgresql, базы]\ncategory: Базы данных\n"
        "author: Анна\ncreated: 2026-09-14\n---\nИндексы ускоряют поиск строк в таблице."
    ),
    "bm25": (
        "---\ntitle: Алгоритм BM25\ntags: [поиск, алгоритмы]\ncategory: Алгоритмы\n"
        "author: Борис\ncreated: 2026-10-02\n---\nОценка релевантности документа запросу."
    ),
    "redis": (
        "---\ntitle: Кэш Redis\ntags: [redis, базы]\ncategory: базы данных\nauthor: Анна\n"
        "created: 2026-08-01\n---\nКэш ускоряет ответы и снижает нагрузку."
    ),
    "note": "# Заметка без шапки\n\nПоиск по заметкам и `индексы` в коде.",
}


@pytest.fixture
def engine(engine_from) -> SearchEngine:
    return engine_from(DOCS)


def ids(result) -> list[str]:
    return [hit.doc_id for hit in result.hits]


def test_finds_all_word_forms(engine):
    assert set(ids(engine.search(SearchQuery("индексов")))) == {"pg", "note"}


def test_results_are_sorted_by_relevance(engine):
    result = engine.search(SearchQuery("индексы"))
    assert ids(result)[0] == "pg"
    assert [h.score for h in result.hits] == sorted((h.score for h in result.hits), reverse=True)


@pytest.mark.parametrize("scorer", ["bm25", "tfidf", "hybrid"])
def test_every_scorer_can_be_selected(engine, scorer):
    assert "pg" in ids(engine.search(SearchQuery("индексы", scorer=scorer)))


def test_unknown_scorer_is_an_error(engine):
    with pytest.raises(UnknownScorerError):
        engine.search(SearchQuery("индексы", scorer="нет"))


def test_query_of_stop_words_finds_nothing(engine):
    result = engine.search(SearchQuery("и в на"))
    assert result.total == 0
    assert result.hits == ()


def test_unknown_word_finds_nothing(engine):
    assert engine.search(SearchQuery("абракадабра")).total == 0


def test_empty_query_lists_all_documents(engine):
    assert engine.search(SearchQuery("", sort="title")).total == 4


def test_filter_by_tags_requires_all(engine):
    only_bases = SearchFilters(tags=("базы",))
    both = SearchFilters(tags=("базы", "redis"))
    assert set(ids(engine.search(SearchQuery(filters=only_bases)))) == {"pg", "redis"}
    assert ids(engine.search(SearchQuery(filters=both))) == ["redis"]


def test_filter_by_category_ignores_case(engine):
    filters = SearchFilters(category="БАЗЫ ДАННЫХ")
    assert set(ids(engine.search(SearchQuery(filters=filters)))) == {"pg", "redis"}


def test_filter_by_author_ignores_case(engine):
    filters = SearchFilters(author="анна")
    assert set(ids(engine.search(SearchQuery(filters=filters)))) == {"pg", "redis"}


def test_filter_by_date_range_excludes_documents_without_date(engine):
    since = SearchFilters(created_from=date(2026, 9, 1))
    until = SearchFilters(created_to=date(2026, 9, 1))
    assert set(ids(engine.search(SearchQuery(filters=since)))) == {"pg", "bm25"}
    assert ids(engine.search(SearchQuery(filters=until))) == ["redis"]


def test_filters_combine_with_text(engine):
    query = SearchQuery("ускоряет", filters=SearchFilters(tags=("redis",)))
    assert ids(engine.search(query)) == ["redis"]


def test_sort_by_date_puts_documents_without_date_last(engine):
    assert ids(engine.search(SearchQuery(sort="created"))) == ["bm25", "pg", "redis", "note"]


def test_sort_by_title(engine):
    titles = [h.title for h in engine.search(SearchQuery(sort="title")).hits]
    assert titles == sorted(titles, key=str.casefold)


def test_pagination_keeps_total(engine):
    first = engine.search(SearchQuery(sort="title", limit=2))
    second = engine.search(SearchQuery(sort="title", limit=2, offset=2))
    assert first.total == second.total == 4
    assert len(first.hits) == len(second.hits) == 2
    assert not set(ids(first)) & set(ids(second))


@pytest.mark.parametrize(
    "query",
    [
        SearchQuery(sort="дата"),
        SearchQuery(limit=0),
        SearchQuery(limit=101),
        SearchQuery(offset=-1),
    ],
)
def test_invalid_parameters_are_rejected(engine, query):
    with pytest.raises(InvalidQueryError):
        engine.search(query)


def test_hit_contains_metadata_and_highlight(engine):
    hit = engine.search(SearchQuery("индексы")).hits[0]
    assert hit.title == "Индексы PostgreSQL"
    assert hit.tags == ("postgresql", "базы")
    assert hit.category == "Базы данных"
    assert hit.created == date(2026, 9, 14)
    assert "<mark>Индексы</mark>" in hit.snippet.to_html()


def test_snippet_shows_match_found_only_in_code(engine_from):
    engine = engine_from({"a": "# Заметка\n\nОбычный текст.\n\n```\nsearch_index = build()\n```\n"})
    hit = engine.search(SearchQuery("build")).hits[0]
    assert "<mark>build</mark>" in hit.snippet.to_html()


def test_snippet_shows_match_found_only_in_heading(engine_from):
    engine = engine_from({"a": "# Заметка\n\n## Настройка кэша\n\nОбычный текст."})
    hit = engine.search(SearchQuery("кэш")).hits[0]
    assert "<mark>кэша</mark>" in hit.snippet.to_html()


def test_updated_document_replaces_old_text(engine):
    engine.add_document("pg", parse_document("# Новый текст\n\nТеперь про репликацию."))
    assert ids(engine.search(SearchQuery("репликация"))) == ["pg"]
    assert "pg" not in ids(engine.search(SearchQuery("таблице")))
    assert len(engine) == 4


def test_removed_document_is_not_found(engine):
    engine.remove_document("redis")
    assert engine.search(SearchQuery("кэш")).total == 0
    assert len(engine) == 3
    with pytest.raises(DocumentNotFoundError):
        engine.get_document("redis")


def test_removing_unknown_document_is_an_error(engine):
    with pytest.raises(DocumentNotFoundError):
        engine.remove_document("нет")
