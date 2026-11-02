"""Фрагмент текста с подсветкой совпадений для выдачи поиска."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from html import escape

from kb.textproc import tokenize

DEFAULT_SNIPPET_CHARS = 240
_CONTEXT_CHARS = 40


@dataclass(frozen=True, slots=True)
class Snippet:
    """Текст фрагмента и позиции совпадений в нём."""

    text: str
    spans: tuple[tuple[int, int], ...] = ()

    def to_html(self, tag: str = "mark") -> str:
        """HTML-фрагмент. Текст экранируется: документ не должен внедрить разметку в страницу."""
        parts: list[str] = []
        position = 0
        for start, end in self.spans:
            parts.append(escape(self.text[position:start]))
            parts.append(f"<{tag}>{escape(self.text[start:end])}</{tag}>")
            position = end
        parts.append(escape(self.text[position:]))
        return "".join(parts)


def build_snippet(
    text: str, query_terms: Collection[str], max_chars: int = DEFAULT_SNIPPET_CHARS
) -> Snippet:
    """Выбирает окно с наибольшим числом разных терминов запроса и отмечает совпадения."""
    matches = [token for token in tokenize(text) if token.term in query_terms]
    if not matches:
        return Snippet(_clip(text, 0, max_chars))

    best_start = matches[0].start
    best_distinct = 0
    for first, anchor in enumerate(matches):
        window_end = anchor.start + max_chars
        distinct = {m.term for m in matches[first:] if m.end <= window_end}
        if len(distinct) > best_distinct:
            best_distinct = len(distinct)
            best_start = anchor.start

    start = _word_boundary(text, max(0, best_start - _CONTEXT_CHARS))
    end = min(len(text), start + max_chars)
    clipped = _clip(text, start, end)
    shift = 1 if start > 0 else 0
    spans = tuple(
        (m.start - start + shift, m.end - start + shift)
        for m in matches
        if m.start >= start and m.end <= end
    )
    return Snippet(clipped, spans)


def _word_boundary(text: str, position: int) -> int:
    """Сдвигает начало фрагмента вперёд, чтобы он не начинался с середины слова."""
    if position == 0:
        return 0
    space = text.find(" ", position)
    return space + 1 if space != -1 else position


def _clip(text: str, start: int, end: int) -> str:
    fragment = text[start:end].replace("\n", " ")
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{fragment}{suffix}"
