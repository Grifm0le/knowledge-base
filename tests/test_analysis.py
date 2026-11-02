from kb.analysis import analyze, build_outline
from kb.models import Heading
from kb.parser import parse_document


def test_counts_and_reading_time():
    words = " ".join(["слово"] * 400)
    text = (
        "---\ntags: [a]\n---\n# Т\n\n"
        f"{words}\n\n```python\nx = 1\n```\n\n```sql\nSELECT 1\n```\n\n[a](http://x)"
    )
    result = analyze(parse_document(text))
    assert result.words == 401
    assert result.headings == 1
    assert result.max_heading_level == 1
    assert result.code_blocks == 2
    assert result.code_languages == ("python", "sql")
    assert result.links == 1
    assert result.reading_minutes == 2
    assert result.warnings == ()


def test_reading_time_is_at_least_a_minute_for_short_text():
    assert analyze(parse_document("Текст")).reading_minutes == 1


def test_outline_is_nested():
    headings = (
        Heading(1, "А", 1),
        Heading(2, "Б", 2),
        Heading(3, "В", 3),
        Heading(2, "Г", 4),
        Heading(1, "Д", 5),
    )
    roots = build_outline(headings)
    assert [node.text for node in roots] == ["А", "Д"]
    assert [node.text for node in roots[0].children] == ["Б", "Г"]
    assert [node.text for node in roots[0].children[0].children] == ["В"]


def test_warnings_for_document_without_metadata_and_headings():
    warnings = analyze(parse_document("Текст")).warnings
    assert "Нет тегов" in warnings
    assert "Нет заголовков" in warnings
    assert any("Нет названия" in w for w in warnings)


def test_warning_for_skipped_heading_level():
    warnings = analyze(parse_document("# А\n\n### Б\n")).warnings
    assert "Строка 3: пропущен уровень заголовка (H1 → H3)" in warnings


def test_warning_for_repeated_heading_and_empty_link():
    warnings = analyze(parse_document("# А\n\n# а\n\n[пусто]()\n")).warnings
    assert "Заголовок повторяется 2 раза: 'а'" in warnings
    assert "Ссылка без адреса: 'пусто'" in warnings


def test_warning_for_code_without_language():
    warnings = analyze(parse_document("# А\n\n```\nкод\n```\n")).warnings
    assert "Строка 3: блок кода без указания языка" in warnings
