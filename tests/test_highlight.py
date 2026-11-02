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
