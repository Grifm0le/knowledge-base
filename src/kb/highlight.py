"""Фрагмент текста с подсветкой совпадений для выдачи поиска."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from html import escape

from kb.textproc import Token, tokenize

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

    best_start = _best_window_start(matches, max_chars)
    context = min(_CONTEXT_CHARS, max_chars // 4)
    start = _word_boundary(text, max(0, best_start - context), best_start)
    end = min(len(text), start + max_chars)
    clipped = _clip(text, start, end)
    shift = 1 if start > 0 else 0
    # Слово может не поместиться в окно целиком: тогда отмечаем его видимую часть.
    spans = tuple(
        (max(m.start, start) - start + shift, min(m.end, end) - start + shift)
        for m in matches
        if m.start < end and m.end > start
    )
    return Snippet(clipped, spans)


def _best_window_start(matches: Sequence[Token], max_chars: int) -> int:
    """Начало окна с наибольшим числом разных терминов: скользящее окно за линейное время."""
    best_start = matches[0].start
    best_distinct = 0
    in_window: Counter[str] = Counter()
    right = 0
    for left, anchor in enumerate(matches):
        window_end = anchor.start + max_chars
        while right < len(matches) and (right == left or matches[right].end <= window_end):
            in_window[matches[right].term] += 1
            right += 1
        if len(in_window) > best_distinct:
            best_distinct = len(in_window)
            best_start = anchor.start
        in_window[anchor.term] -= 1
        if not in_window[anchor.term]:
            del in_window[anchor.term]
    return best_start


def _word_boundary(text: str, position: int, limit: int) -> int:
    """Сдвигает начало фрагмента вперёд до пробела, но не дальше первого совпадения."""
    if position == 0:
        return 0
    space = text.find(" ", position, limit)
    return space + 1 if space != -1 else position


def _clip(text: str, start: int, end: int) -> str:
    fragment = text[start:end].replace("\n", " ")
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{fragment}{suffix}"
