"""Обратный индекс: термин → документы → частота термина по полям."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from kb.textproc import terms

FieldCounts = dict[str, int]


class InvertedIndex:
    """Индекс в памяти с добавлением, заменой и удалением документов."""

    def __init__(self) -> None:
        self._postings: dict[str, dict[str, FieldCounts]] = {}
        self._field_lengths: dict[str, FieldCounts] = {}
        self._doc_terms: dict[str, set[str]] = {}
        self._total_lengths: Counter[str] = Counter()

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self._field_lengths

    @property
    def doc_count(self) -> int:
        return len(self._field_lengths)

    @property
    def term_count(self) -> int:
        return len(self._postings)

    def add(self, doc_id: str, fields: Mapping[str, str]) -> None:
        """Индексирует документ. Если он уже есть, старая версия заменяется."""
        if doc_id in self:
            self.remove(doc_id)
        lengths: FieldCounts = {}
        doc_terms: set[str] = set()
        for field_name, text in fields.items():
            counts = Counter(terms(text))
            lengths[field_name] = sum(counts.values())
            for term, frequency in counts.items():
                self._postings.setdefault(term, {}).setdefault(doc_id, {})[field_name] = frequency
                doc_terms.add(term)
        self._field_lengths[doc_id] = lengths
        self._doc_terms[doc_id] = doc_terms
        self._total_lengths.update(lengths)

    def remove(self, doc_id: str) -> None:
        """Удаляет документ. Термины, оставшиеся без документов, удаляются тоже."""
        for term in self._doc_terms.pop(doc_id, ()):
            postings = self._postings[term]
            del postings[doc_id]
            if not postings:
                del self._postings[term]
        self._total_lengths.subtract(self._field_lengths.pop(doc_id, {}))

    def postings(self, term: str) -> Mapping[str, FieldCounts]:
        """Документы с термином и его частота по полям."""
        return self._postings.get(term, {})

    def doc_frequency(self, term: str) -> int:
        return len(self._postings.get(term, ()))

    def field_lengths(self, doc_id: str) -> FieldCounts:
        return self._field_lengths[doc_id]

    def doc_ids(self) -> list[str]:
        return list(self._field_lengths)

    def average_weighted_length(self, weights: Mapping[str, float]) -> float:
        """Средняя взвешенная длина документа: нужна BM25 для нормализации."""
        if not self._field_lengths:
            return 0.0
        total = sum(weights.get(name, 1.0) * size for name, size in self._total_lengths.items())
        return total / len(self._field_lengths)
