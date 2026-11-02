"""Методы скоринга релевантности. Новый метод подключается через register_scorer."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import ClassVar

from kb.errors import UnknownScorerError
from kb.index import FieldCounts, InvertedIndex

DEFAULT_FIELD_WEIGHTS: Mapping[str, float] = {
    "title": 3.0,
    "headings": 2.0,
    "tags": 2.0,
    "body": 1.0,
    "code": 0.5,
}


class Scorer(ABC):
    """Метод скоринга: по терминам запроса возвращает оценки найденных документов."""

    name: ClassVar[str]

    def __init__(self, field_weights: Mapping[str, float] | None = None) -> None:
        self.field_weights = dict(field_weights or DEFAULT_FIELD_WEIGHTS)

    def weighted_tf(self, counts: FieldCounts) -> float:
        """Частота термина с учётом веса поля: совпадение в заголовке важнее, чем в тексте."""
        return sum(self.field_weights.get(name, 1.0) * tf for name, tf in counts.items())

    def weighted_length(self, lengths: FieldCounts) -> float:
        return sum(self.field_weights.get(name, 1.0) * size for name, size in lengths.items())

    @abstractmethod
    def score(self, query_terms: Sequence[str], index: InvertedIndex) -> dict[str, float]:
        """Оценка каждого документа, где есть хотя бы один термин запроса."""


_registry: dict[str, Callable[..., Scorer]] = {}


def register_scorer(cls: type[Scorer]) -> type[Scorer]:
    """Регистрирует класс метода под его именем."""
    _registry[cls.name] = cls
    return cls


def available_scorers() -> list[str]:
    return sorted(_registry)


def create_scorer(name: str, **params: object) -> Scorer:
    """Создаёт метод по имени."""
    try:
        factory = _registry[name]
    except KeyError:
        raise UnknownScorerError(
            f"Неизвестный метод скоринга {name!r}. Доступны: {', '.join(available_scorers())}"
        ) from None
    return factory(**params)


@register_scorer
class TfIdfScorer(Scorer):
    """TF-IDF с логарифмической частотой и нормализацией на длину документа."""

    name = "tfidf"

    def score(self, query_terms: Sequence[str], index: InvertedIndex) -> dict[str, float]:
        doc_count = index.doc_count
        scores: dict[str, float] = {}
        for term, query_tf in Counter(query_terms).items():
            postings = index.postings(term)
            if not postings:
                continue
            idf = math.log((doc_count + 1) / (len(postings) + 1)) + 1
            for doc_id, counts in postings.items():
                tf = self.weighted_tf(counts)
                if not tf:  # термин только в полях с нулевым весом: совпадением не считается
                    continue
                weight = math.log1p(tf) * idf * query_tf
                scores[doc_id] = scores.get(doc_id, 0.0) + weight
        for doc_id in scores:
            scores[doc_id] /= math.sqrt(self.weighted_length(index.field_lengths(doc_id)))
        return scores


@register_scorer
class Bm25Scorer(Scorer):
    """Okapi BM25: насыщение частоты термина (k1) и нормализация на длину (b)."""

    name = "bm25"

    def __init__(
        self,
        field_weights: Mapping[str, float] | None = None,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        super().__init__(field_weights)
        self.k1 = k1
        self.b = b

    def score(self, query_terms: Sequence[str], index: InvertedIndex) -> dict[str, float]:
        doc_count = index.doc_count
        average_length = index.average_weighted_length(self.field_weights)
        lengths: dict[str, float] = {}
        scores: dict[str, float] = {}
        for term, query_tf in Counter(query_terms).items():
            postings = index.postings(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            for doc_id, counts in postings.items():
                tf = self.weighted_tf(counts)
                if not tf:  # термин только в полях с нулевым весом: совпадением не считается
                    continue
                length = lengths.get(doc_id)
                if length is None:
                    length = self.weighted_length(index.field_lengths(doc_id))
                    lengths[doc_id] = length
                norm = self.k1 * (1 - self.b + self.b * length / average_length)
                weight = idf * tf * (self.k1 + 1) / (tf + norm) * query_tf
                scores[doc_id] = scores.get(doc_id, 0.0) + weight
        return scores


@register_scorer
class HybridScorer(Scorer):
    """Гибридное ранжирование: взвешенная сумма нормализованных оценок нескольких методов."""

    name = "hybrid"

    def __init__(
        self,
        field_weights: Mapping[str, float] | None = None,
        method_weights: Mapping[str, float] | None = None,
    ) -> None:
        super().__init__(field_weights)
        self.method_weights = dict(method_weights or {"bm25": 0.7, "tfidf": 0.3})

    def score(self, query_terms: Sequence[str], index: InvertedIndex) -> dict[str, float]:
        combined: dict[str, float] = {}
        for method, weight in self.method_weights.items():
            scorer = create_scorer(method, field_weights=self.field_weights)
            for doc_id, value in _min_max(scorer.score(query_terms, index)).items():
                combined[doc_id] = combined.get(doc_id, 0.0) + weight * value
        return combined


def _min_max(scores: Mapping[str, float]) -> dict[str, float]:
    """Приводит оценки к диапазону 0–1, иначе шкалы разных методов нельзя сложить."""
    if not scores:
        return {}
    low, high = min(scores.values()), max(scores.values())
    if high == low:
        return dict.fromkeys(scores, 1.0)
    return {doc_id: (value - low) / (high - low) for doc_id, value in scores.items()}
