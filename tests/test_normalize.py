from datetime import date, datetime

import pytest

from kb.errors import DocumentValidationError
from kb.normalize import (
    MAX_DOCUMENT_BYTES,
    decode_document,
    normalize_date,
    normalize_string,
    normalize_tags,
    normalize_text,
)


def test_decode_strips_bom():
    assert decode_document("﻿текст".encode()) == "текст"


def test_decode_rejects_other_encodings():
    with pytest.raises(DocumentValidationError) as error:
        decode_document("текст".encode("cp1251"))
    assert error.value.code == "invalid_encoding"


def test_decode_rejects_too_large_document():
    with pytest.raises(DocumentValidationError) as error:
        decode_document(b"x" * (MAX_DOCUMENT_BYTES + 1))
    assert error.value.code == "document_too_large"


def test_normalize_text_line_endings_and_trailing_spaces():
    assert normalize_text("a  \r\nb\t\rc") == "a\nb\nc"


def test_normalize_text_removes_invisible_characters():
    assert normalize_text("по​иск") == "поиск"


def test_normalize_text_composes_unicode():
    assert normalize_text("й") == "й"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ()),
        ("Git, #Docker ,  ", ("git", "docker")),
        (["Базы Данных", "базы данных", "SQL"], ("базы-данных", "sql")),
        ([2026, "x"], ("2026", "x")),
    ],
)
def test_normalize_tags(value, expected):
    assert normalize_tags(value) == expected


@pytest.mark.parametrize("value", [{"a": 1}, 5, [["вложенный"]], [{"a": 1}]])
def test_normalize_tags_rejects_wrong_types(value):
    with pytest.raises(DocumentValidationError) as error:
        normalize_tags(value)
    assert error.value.code == "invalid_tags"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (date(2026, 1, 2), date(2026, 1, 2)),
        (datetime(2026, 1, 2, 3, 4), date(2026, 1, 2)),
        (" 2026-01-02 ", date(2026, 1, 2)),
    ],
)
def test_normalize_date(value, expected):
    assert normalize_date(value) == expected


@pytest.mark.parametrize("value", ["02.01.2026", "вчера", 20260102])
def test_normalize_date_rejects_bad_values(value):
    with pytest.raises(DocumentValidationError) as error:
        normalize_date(value)
    assert error.value.code == "invalid_date"


def test_normalize_string():
    assert normalize_string("  Базы   данных ", "category") == "Базы данных"
    assert normalize_string("   ", "category") is None
    assert normalize_string(None, "category") is None
    assert normalize_string(7, "category") == "7"


def test_normalize_string_rejects_collections():
    with pytest.raises(DocumentValidationError) as error:
        normalize_string(["a"], "author")
    assert error.value.code == "invalid_author"
