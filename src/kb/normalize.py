"""Валидация и нормализация текста и метаданных документа."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime

from kb.errors import DocumentValidationError

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024

# Невидимые символы, которые ломают поиск по слову: слово с ними не совпадёт с запросом.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"))


def decode_document(data: bytes) -> str:
    """Декодирует файл как UTF-8. Другие кодировки не угадываем, а отклоняем."""
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentValidationError(
            "document_too_large",
            f"Документ больше {MAX_DOCUMENT_BYTES // (1024 * 1024)} МБ: {len(data)} байт",
        )
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentValidationError(
            "invalid_encoding", f"Документ не в UTF-8: байт {exc.start}"
        ) from exc


def normalize_text(raw: str) -> str:
    """Приводит текст к NFC, убирает невидимые символы, CRLF и пробелы в конце строк."""
    text = unicodedata.normalize("NFC", raw).translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip(" \t") for line in text.split("\n"))


def normalize_tags(value: object) -> tuple[str, ...]:
    """Теги: строка через запятую или список. Нижний регистр, без `#`, без повторов."""
    if value is None:
        return ()
    if isinstance(value, str):
        items: list[object] = list(value.split(","))
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise DocumentValidationError(
            "invalid_tags",
            f"Теги должны быть строкой или списком, получено: {type(value).__name__}",
        )
    tags: dict[str, None] = {}
    for item in items:
        if isinstance(item, (dict, list, tuple)):
            raise DocumentValidationError("invalid_tags", f"Некорректный тег: {item!r}")
        tag = "-".join(str(item).strip().lstrip("#").lower().split())
        if tag:
            tags[tag] = None
    return tuple(tags)


def normalize_date(value: object) -> date | None:
    """Дата из front matter: объект YAML или строка ГГГГ-ММ-ДД."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise DocumentValidationError(
                "invalid_date", f"Дата не в формате ГГГГ-ММ-ДД: {value!r}"
            ) from exc
    raise DocumentValidationError("invalid_date", f"Некорректная дата: {value!r}")


def normalize_string(value: object, field_name: str) -> str | None:
    """Строковое поле метаданных: обрезает пробелы, пустое значение считает отсутствующим."""
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        raise DocumentValidationError(
            f"invalid_{field_name}", f"Поле {field_name} должно быть строкой: {value!r}"
        )
    return " ".join(str(value).split()) or None
