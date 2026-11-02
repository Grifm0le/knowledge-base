"""Импорт Markdown-файлов из каталога в поисковый движок."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from kb.errors import DocumentValidationError
from kb.models import ParsedDocument
from kb.normalize import decode_document
from kb.parser import parse_document
from kb.search import SearchEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImportFailure:
    path: str
    code: str
    message: str


@dataclass(slots=True)
class ImportReport:
    imported: list[str] = field(default_factory=list)
    failed: list[ImportFailure] = field(default_factory=list)


def read_document(path: Path) -> ParsedDocument:
    """Читает и разбирает один файл. Имя файла служит запасным названием."""
    return parse_document(decode_document(path.read_bytes()), fallback_title=path.stem)


def import_directory(engine: SearchEngine, root: Path) -> ImportReport:
    """Импортирует все .md из каталога. Плохой файл не прерывает импорт остальных."""
    report = ImportReport()
    for path in sorted(root.rglob("*.md")):
        doc_id = path.relative_to(root).as_posix()
        try:
            engine.add_document(doc_id, read_document(path))
        except DocumentValidationError as exc:
            logger.warning("Файл %s отклонён: %s", doc_id, exc.message)
            report.failed.append(ImportFailure(doc_id, exc.code, exc.message))
        except OSError as exc:
            logger.error("Файл %s не прочитан: %s", doc_id, exc)
            report.failed.append(ImportFailure(doc_id, "read_error", str(exc)))
        else:
            report.imported.append(doc_id)
    logger.info("Импорт: принято %d, отклонено %d", len(report.imported), len(report.failed))
    return report
