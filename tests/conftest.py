from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from kb.importer import import_directory
from kb.parser import parse_document
from kb.search import SearchEngine

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def engine_from() -> Callable[[dict[str, str]], SearchEngine]:
    """Собирает движок из словаря «идентификатор → Markdown»."""

    def build(documents: dict[str, str]) -> SearchEngine:
        engine = SearchEngine()
        for doc_id, text in documents.items():
            engine.add_document(doc_id, parse_document(text))
        return engine

    return build


@pytest.fixture
def samples_engine() -> SearchEngine:
    """Движок с реальными примерами документов из каталога samples."""
    engine = SearchEngine()
    report = import_directory(engine, SAMPLES)
    assert not report.failed
    return engine
