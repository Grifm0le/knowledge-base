"""Анализ структуры и содержимого разобранного документа."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from kb.models import Heading, ParsedDocument
from kb.parser import UNTITLED

WORDS_PER_MINUTE = 200


@dataclass(slots=True)
class OutlineNode:
    """Узел оглавления: заголовок и вложенные в него разделы."""

    text: str
    level: int
    children: list[OutlineNode] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DocumentAnalysis:
    words: int
    characters: int
    headings: int
    max_heading_level: int
    code_blocks: int
    code_languages: tuple[str, ...]
    links: int
    reading_minutes: int
    outline: tuple[OutlineNode, ...]
    warnings: tuple[str, ...]


def build_outline(headings: tuple[Heading, ...]) -> tuple[OutlineNode, ...]:
    """Строит дерево разделов из плоского списка заголовков."""
    roots: list[OutlineNode] = []
    stack: list[OutlineNode] = []
    for heading in headings:
        node = OutlineNode(heading.text, heading.level)
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)
    return tuple(roots)


def collect_warnings(document: ParsedDocument) -> tuple[str, ...]:
    """Замечания к документу: принять можно, но автору стоит поправить."""
    warnings: list[str] = []
    if document.metadata.title == UNTITLED:
        warnings.append("Нет названия: ни поля title, ни заголовка первого уровня")
    if not document.metadata.tags:
        warnings.append("Нет тегов")
    if not document.headings:
        warnings.append("Нет заголовков")

    previous_level = 0
    for heading in document.headings:
        if previous_level and heading.level > previous_level + 1:
            warnings.append(
                f"Строка {heading.line}: пропущен уровень заголовка "
                f"(H{previous_level} → H{heading.level})"
            )
        previous_level = heading.level

    counts = Counter(h.text.casefold() for h in document.headings)
    warnings.extend(
        f"Заголовок повторяется {count} раза: {text!r}"
        for text, count in counts.items()
        if count > 1
    )
    warnings.extend(f"Ссылка без адреса: {link.text!r}" for link in document.links if not link.url)
    warnings.extend(
        f"Строка {block.line}: блок кода без указания языка"
        for block in document.code_blocks
        if block.language is None
    )
    return tuple(warnings)


def analyze(document: ParsedDocument) -> DocumentAnalysis:
    """Считает показатели документа и собирает замечания."""
    words = len(document.body.split())
    return DocumentAnalysis(
        words=words,
        characters=len(document.body),
        headings=len(document.headings),
        max_heading_level=max((h.level for h in document.headings), default=0),
        code_blocks=len(document.code_blocks),
        code_languages=tuple(sorted({c.language for c in document.code_blocks if c.language})),
        links=len(document.links),
        reading_minutes=max(1, round(words / WORDS_PER_MINUTE)) if words else 0,
        outline=build_outline(document.headings),
        warnings=collect_warnings(document),
    )
