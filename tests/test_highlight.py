from kb.highlight import Snippet, build_snippet
from kb.textproc import terms


def marked(snippet: Snippet) -> list[str]:
    return [snippet.text[start:end] for start, end in snippet.spans]


def test_matches_are_found_in_any_word_form():
    snippet = build_snippet("Здесь индексы, а там индекса нет.", set(terms("индекс")))
    assert marked(snippet) == ["индексы", "индекса"]


def test_html_marks_matches():
    snippet = build_snippet("Быстрый поиск", set(terms("поиск")))
    assert snippet.to_html() == "Быстрый <mark>поиск</mark>"
    assert snippet.to_html(tag="b") == "Быстрый <b>поиск</b>"


def test_html_escapes_document_text():
    snippet = build_snippet("<script>alert(1)</script> поиск", set(terms("поиск")))
    html = snippet.to_html()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<mark>поиск</mark>" in html


def test_no_match_returns_beginning_of_text():
    snippet = build_snippet("слово " * 100, set(terms("поиск")), max_chars=20)
    assert snippet.spans == ()
    assert snippet.text == "слово слово слово сл…"


def test_empty_text():
    assert build_snippet("", set(terms("поиск"))) == Snippet("")


def test_snippet_is_cut_around_match_with_ellipsis():
    text = "начало " * 50 + "поиск " + "конец " * 50
    snippet = build_snippet(text, set(terms("поиск")), max_chars=80)
    assert snippet.text.startswith("…")
    assert snippet.text.endswith("…")
    assert marked(snippet) == ["поиск"]


def test_window_with_most_distinct_terms_is_chosen():
    text = "поиск " + "слово " * 100 + "индекс кэш поиск " + "слово " * 100
    snippet = build_snippet(text, set(terms("поиск индекс кэш")), max_chars=60)
    assert set(marked(snippet)) == {"индекс", "кэш", "поиск"}


def test_newlines_are_replaced_without_shifting_marks():
    snippet = build_snippet("первая строка\n\nпоиск", set(terms("поиск")))
    assert marked(snippet) == ["поиск"]
    assert "\n" not in snippet.text


def test_snippet_does_not_start_in_the_middle_of_a_word():
    text = "длинноеслово " * 20 + "поиск"
    snippet = build_snippet(text, set(terms("поиск")), max_chars=60)
    assert snippet.text.lstrip("…").startswith("длинноеслово")


def test_word_longer_than_window_does_not_break_selection():
    long_word = "поиск" + "о" * 50
    text = f"{long_word} индекс"
    snippet = build_snippet(text, {*terms(long_word), *terms("индекс")}, max_chars=20)
    assert marked(snippet)


def test_large_document_with_many_matches_is_fast():
    text = "поиск индекс " * 20000
    snippet = build_snippet(text, set(terms("поиск индекс")))
    assert len(snippet.text) <= 242
    assert marked(snippet)


def test_text_without_spaces_keeps_match_in_snippet():
    text = ",".join(["слово"] * 100) + ",поиск," + ",".join(["слово"] * 100)
    snippet = build_snippet(text, set(terms("поиск")), max_chars=60)
    assert marked(snippet) == ["поиск"]


def test_match_cut_by_window_is_marked_partially():
    text = "начало " + "поиск" * 30
    snippet = build_snippet(text, set(terms("поиск" * 30)), max_chars=30)
    assert len(marked(snippet)) == 1
    assert marked(snippet)[0].startswith("поиск")
    assert snippet.text.endswith("…")
