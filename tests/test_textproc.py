from concurrent.futures import ThreadPoolExecutor

import pytest

from kb.textproc import normalize_word, stem, terms, tokenize


@pytest.mark.parametrize(
    "forms",
    [
        ("документ", "документа", "документы", "документов"),
        ("индекс", "индекса", "индексы", "индексов"),
        ("search", "searching", "searches"),
    ],
)
def test_word_forms_share_one_stem(forms):
    assert len({stem(word) for word in forms}) == 1


def test_numbers_are_not_stemmed():
    assert stem("2026") == "2026"


def test_mixed_alphabet_uses_russian_stemmer():
    assert stem("sqlзапросы") == stem("sqlзапрос")


def test_case_and_yo_do_not_matter():
    assert normalize_word("ЁЛКА") == "елка"
    assert terms("Ёлка") == terms("елка")


def test_stop_words_are_removed():
    assert terms("поиск и индекс для базы the search") == terms("поиск индекс базы search")
    assert terms("и в на the") == []


def test_tokens_keep_positions_of_original_words():
    text = "Быстрый ПОИСК, по базе"
    tokens = list(tokenize(text))
    assert [text[t.start : t.end] for t in tokens] == ["Быстрый", "ПОИСК", "базе"]


def test_underscore_and_hyphen_split_words():
    assert terms("full-text") == terms("full text")
    assert terms("user_name") == terms("user name")


def test_empty_text():
    assert terms("") == []
    assert terms("!!! ... ---") == []


def test_stem_gives_same_result_from_many_threads():
    words = [f"документ{suffix}" for suffix in ("", "а", "у", "ом", "е", "ы", "ов", "ами", "ах")]
    expected = stem("документ")
    stem.cache_clear()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(stem, words * 200))
    assert set(results) == {expected}


def test_terms_and_tokenize_give_same_result():
    text = "Быстрый поиск по базе и the searching ёлка 2026 user_name"
    assert terms(text) == [token.term for token in tokenize(text)]
