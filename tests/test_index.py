import pytest

from kb.index import InvertedIndex
from kb.textproc import stem


def build() -> InvertedIndex:
    index = InvertedIndex()
    index.add("a", {"title": "поиск", "body": "поиск индекс индекс"})
    index.add("b", {"title": "кэш", "body": "индекс"})
    return index


def test_postings_keep_frequency_by_field():
    index = build()
    assert index.postings(stem("индекс")) == {"a": {"body": 2}, "b": {"body": 1}}
    assert index.postings(stem("поиск")) == {"a": {"title": 1, "body": 1}}


def test_counts():
    index = build()
    assert index.doc_count == 2
    assert index.term_count == 3
    assert index.doc_frequency(stem("индекс")) == 2
    assert index.doc_frequency("нет-такого") == 0
    assert index.postings("нет-такого") == {}
    assert "a" in index
    assert "c" not in index


def test_field_lengths():
    assert build().field_lengths("a") == {"title": 1, "body": 3}


def test_average_weighted_length():
    index = build()
    assert index.average_weighted_length({"title": 2.0, "body": 1.0}) == pytest.approx(4.0)


def test_average_length_of_empty_index():
    assert InvertedIndex().average_weighted_length({"body": 1.0}) == 0.0


def test_remove_cleans_terms_without_documents():
    index = build()
    index.remove("a")
    assert index.doc_count == 1
    assert index.postings(stem("поиск")) == {}
    assert index.doc_frequency(stem("индекс")) == 1
    assert index.term_count == 2


def test_remove_unknown_document_does_nothing():
    index = build()
    index.remove("нет")
    assert index.doc_count == 2


def test_add_replaces_previous_version():
    index = build()
    index.add("a", {"body": "кэш"})
    assert index.doc_count == 2
    assert index.postings(stem("поиск")) == {}
    assert index.postings(stem("индекс")) == {"b": {"body": 1}}
    assert index.field_lengths("a") == {"body": 1}


def test_doc_ids_and_empty_field():
    index = InvertedIndex()
    index.add("a", {"title": "", "body": "слово"})
    assert index.doc_ids() == ["a"]
    assert index.field_lengths("a") == {"title": 0, "body": 1}


def test_average_length_stays_correct_after_replace_and_remove():
    index = build()
    index.add("a", {"body": "кэш"})
    assert index.average_weighted_length({"body": 1.0, "title": 1.0}) == pytest.approx(1.5)
    index.remove("b")
    assert index.average_weighted_length({"body": 1.0, "title": 1.0}) == pytest.approx(1.0)
    index.remove("a")
    assert index.average_weighted_length({"body": 1.0}) == 0.0
