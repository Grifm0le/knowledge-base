"""Структуры данных разобранного документа."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class Metadata:
    """Нормализованные метаданные документа."""

    title: str
    tags: tuple[str, ...] = ()
    category: str | None = None
    author: str | None = None
    created: date | None = None


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str
    line: int


@dataclass(frozen=True, slots=True)
class CodeBlock:
    language: str | None
    text: str
    line: int


@dataclass(frozen=True, slots=True)
class Link:
    text: str
    url: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Результат разбора: метаданные, структура и текст по полям."""

    metadata: Metadata
    body: str
    source: str
    headings: tuple[Heading, ...] = field(default=())
    code_blocks: tuple[CodeBlock, ...] = field(default=())
    links: tuple[Link, ...] = field(default=())

    def index_fields(self) -> dict[str, str]:
        """Текст документа по полям индекса."""
        return {
            "title": self.metadata.title,
            "headings": " ".join(h.text for h in self.headings),
            "tags": " ".join(self.metadata.tags),
            "body": self.body,
            "code": "\n".join(c.text for c in self.code_blocks),
        }
