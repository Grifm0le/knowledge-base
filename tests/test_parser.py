from datetime import date

import pytest

from kb.errors import DocumentValidationError
from kb.parser import UNTITLED, parse_document, split_front_matter

DOCUMENT = """---
title: Заметка
tags: [Поиск, индексы]
category: Базы данных
author: Автор
created: 2026-03-04
---

# Заголовок документа

Первый абзац со [ссылкой](https://example.com/a) и `кодом`.

## Раздел

![схема индекса](pic.png)

```sql
SELECT 1;
```

    отступ как код

| Колонка | Значение |
|---|---|
| ячейка | сорок два |

<div>блок <b>разметки</b></div>
"""


def test_metadata_from_front_matter():
    meta = parse_document(DOCUMENT).metadata
    assert meta.title == "Заметка"
    assert meta.tags == ("поиск", "индексы")
    assert meta.category == "Базы данных"
    assert meta.author == "Автор"
    assert meta.created == date(2026, 3, 4)


def test_headings_keep_level_text_and_source_line():
    headings = parse_document(DOCUMENT).headings
    assert [(h.level, h.text, h.line) for h in headings] == [
        (1, "Заголовок документа", 9),
        (2, "Раздел", 13),
    ]


def test_code_blocks_fenced_and_indented():
    blocks = parse_document(DOCUMENT).code_blocks
    assert [(b.language, b.text.strip()) for b in blocks] == [
        ("sql", "SELECT 1;"),
        (None, "отступ как код"),
    ]


def test_links_are_collected_with_text():
    links = parse_document(DOCUMENT).links
    assert [(link.text, link.url) for link in links] == [("ссылкой", "https://example.com/a")]


def test_body_contains_text_but_not_headings_or_code():
    body = parse_document(DOCUMENT).body
    assert "Первый абзац со ссылкой и кодом." in body
    assert "схема индекса" in body
    assert "сорок два" in body
    assert "блок разметки" in body.replace("  ", " ")
    assert "Заголовок документа" not in body
    assert "SELECT" not in body


def test_title_falls_back_to_first_h1():
    document = parse_document("## Второй\n\n# Первый\n\nТекст")
    assert document.metadata.title == "Первый"


def test_title_falls_back_to_file_name_then_placeholder():
    assert parse_document("Просто текст", fallback_title="файл").metadata.title == "файл"
    assert parse_document("Просто текст").metadata.title == UNTITLED


@pytest.mark.parametrize("text", ["", "   \n\n", "---\ntitle: Только шапка\n---\n"])
def test_empty_document_is_rejected(text):
    with pytest.raises(DocumentValidationError) as error:
        parse_document(text)
    assert error.value.code == "empty_document"


@pytest.mark.parametrize("front", ["title: [не закрыто", "- просто\n- список"])
def test_bad_front_matter_is_rejected(front):
    with pytest.raises(DocumentValidationError) as error:
        parse_document(f"---\n{front}\n---\nТекст")
    assert error.value.code == "invalid_front_matter"


def test_wrong_field_types_are_rejected():
    with pytest.raises(DocumentValidationError) as error:
        parse_document("---\ncreated: вчера\n---\nТекст")
    assert error.value.code == "invalid_date"


def test_empty_front_matter_is_allowed():
    assert split_front_matter("---\n---\nТекст") == ({}, "Текст", 2)


def test_horizontal_rule_is_not_front_matter():
    assert split_front_matter("Текст\n---\nещё") == ({}, "Текст\n---\nещё", 0)


def test_windows_line_endings_do_not_break_front_matter():
    document = parse_document("---\r\ntitle: Заметка\r\n---\r\n\r\nТекст\r\n")
    assert document.metadata.title == "Заметка"
    assert document.body == "Текст"


def test_index_fields_split_document_by_field():
    fields = parse_document(DOCUMENT).index_fields()
    assert fields["title"] == "Заметка"
    assert fields["headings"] == "Заголовок документа Раздел"
    assert fields["tags"] == "поиск индексы"
    assert "SELECT 1;" in fields["code"]
