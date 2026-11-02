"""Поисковый движок: индексация документов, фильтрация, сортировка и выдача."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from kb.errors import DocumentNotFoundError, InvalidQueryError
from kb.highlight import Snippet, build_snippet
from kb.index import InvertedIndex
from kb.models import ParsedDocument
from kb.scoring import create_scorer
from kb.textproc import terms

logger = logging.getLogger(__name__)

SORT_ORDERS = ("relevance", "created", "title")
MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Условия отбора. Теги: документ должен содержать все перечисленные."""

    tags: tuple[str, ...] = ()
    category: str | None = None
    author: str | None = None
    created_from: date | None = None
    created_to: date | None = None

    def matches(self, document: ParsedDocument) -> bool:
        meta = document.metadata
        if not set(self.tags) <= set(meta.tags):
            return False
        if self.category and (meta.category or "").casefold() != self.category.casefold():
            return False
        if self.author and (meta.author or "").casefold() != self.author.casefold():
            return False
        if self.created_from and (meta.created is None or meta.created < self.created_from):
            return False
        return not (self.created_to and (meta.created is None or meta.created > self.created_to))


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str = ""
    filters: SearchFilters = field(default_factory=SearchFilters)
    scorer: str = "bm25"
    sort: str = "relevance"
    limit: int = 10
    offset: int = 0


@dataclass(frozen=True, slots=True)
class SearchHit:
    doc_id: str
    title: str
    score: float
    snippet: Snippet
    tags: tuple[str, ...]
    category: str | None
    created: date | None


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    total: int


class SearchEngine:
    """Хранит разобранные документы и отвечает на поисковые запросы."""

    def __init__(self) -> None:
        self._index = InvertedIndex()
        self._documents: dict[str, ParsedDocument] = {}

    def __len__(self) -> int:
        return len(self._documents)

    @property
    def index(self) -> InvertedIndex:
        return self._index

    def add_document(self, doc_id: str, document: ParsedDocument) -> None:
        self._index.add(doc_id, document.index_fields())
        self._documents[doc_id] = document

    def remove_document(self, doc_id: str) -> None:
        if doc_id not in self._documents:
            raise DocumentNotFoundError(f"Документ не найден: {doc_id}")
        self._index.remove(doc_id)
        del self._documents[doc_id]

    def get_document(self, doc_id: str) -> ParsedDocument:
        try:
            return self._documents[doc_id]
        except KeyError:
            raise DocumentNotFoundError(f"Документ не найден: {doc_id}") from None

    def search(self, query: SearchQuery) -> SearchResult:
        """Выполняет запрос. Без текста возвращает документы, подходящие под фильтры."""
        self._validate(query)
        scorer = create_scorer(query.scorer)
        query_terms = terms(query.text)

        if query.text.strip():
            scores = scorer.score(query_terms, self._index) if query_terms else {}
        else:
            scores = dict.fromkeys(self._documents, 0.0)
        return self._build_result(query, query_terms, scores)

    def rank_scores(self, query: SearchQuery, scores: dict[str, float]) -> SearchResult:
        """Выдача по оценкам, полученным извне, например от полнотекстового поиска базы."""
        self._validate(query)
        known = {doc_id: score for doc_id, score in scores.items() if doc_id in self._documents}
        return self._build_result(query, terms(query.text), known)

    def _build_result(
        self, query: SearchQuery, query_terms: list[str], scores: dict[str, float]
    ) -> SearchResult:
        matched = [
            doc_id for doc_id in scores if query.filters.matches(self._documents[doc_id])
        ]
        ordered = self._sort(matched, scores, query.sort)
        page = ordered[query.offset : query.offset + query.limit]
        logger.debug(
            "Запрос %r: термины %s, метод %s, найдено %d, после фильтров %d",
            query.text, query_terms, query.scorer, len(scores), len(matched),
        )
        return SearchResult(
            hits=tuple(self._hit(doc_id, scores[doc_id], query_terms) for doc_id in page),
            total=len(matched),
        )

    def _validate(self, query: SearchQuery) -> None:
        if query.sort not in SORT_ORDERS:
            raise InvalidQueryError(
                f"Неизвестная сортировка {query.sort!r}. Доступны: {', '.join(SORT_ORDERS)}"
            )
        if not 1 <= query.limit <= MAX_LIMIT:
            raise InvalidQueryError(f"limit должен быть от 1 до {MAX_LIMIT}")
        if query.offset < 0:
            raise InvalidQueryError("offset не может быть отрицательным")

    def _sort(self, doc_ids: list[str], scores: dict[str, float], order: str) -> list[str]:
        if order == "created":
            # Документы без даты идут в конец независимо от порядка сортировки.
            return sorted(
                doc_ids,
                key=lambda d: (
                    self._documents[d].metadata.created is None,
                    -(self._documents[d].metadata.created or date.min).toordinal(),
                    d,
                ),
            )
        if order == "title":
            return sorted(doc_ids, key=lambda d: (self._documents[d].metadata.title.casefold(), d))
        return sorted(doc_ids, key=lambda d: (-scores[d], d))

    def _hit(self, doc_id: str, score: float, query_terms: list[str]) -> SearchHit:
        document = self._documents[doc_id]
        meta = document.metadata
        return SearchHit(
            doc_id=doc_id,
            title=meta.title,
            score=score,
            snippet=_best_snippet(document, set(query_terms)),
            tags=meta.tags,
            category=meta.category,
            created=meta.created,
        )


def _best_snippet(document: ParsedDocument, query_terms: set[str]) -> Snippet:
    """Фрагмент из первого поля с совпадением: термин мог найтись не только в тексте."""
    fields = document.index_fields()
    for name in ("body", "code", "headings"):
        snippet = build_snippet(fields[name], query_terms)
        if snippet.spans:
            return snippet
    return build_snippet(document.body, query_terms)
