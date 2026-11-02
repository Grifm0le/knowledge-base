"""Командная строка: проверка документов, статистика и поиск по каталогу."""

from __future__ import annotations

import argparse
import io
import logging
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from kb.analysis import analyze
from kb.errors import KnowledgeBaseError
from kb.importer import import_directory
from kb.scoring import available_scorers
from kb.search import SORT_ORDERS, SearchEngine, SearchFilters, SearchQuery


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb", description="Личная база знаний")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный журнал (DEBUG)")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="проверить документы и показать замечания")
    check.add_argument("root", type=Path)

    stats = commands.add_parser("stats", help="статистика по базе документов")
    stats.add_argument("root", type=Path)

    search = commands.add_parser("search", help="поиск по документам каталога")
    search.add_argument("root", type=Path)
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--scorer", default="bm25", choices=available_scorers())
    search.add_argument("--sort", default="relevance", choices=SORT_ORDERS)
    search.add_argument("--tag", action="append", default=[], help="можно указать несколько раз")
    search.add_argument("--category")
    search.add_argument("--author")
    search.add_argument("--from", dest="created_from", type=date.fromisoformat)
    search.add_argument("--to", dest="created_to", type=date.fromisoformat)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--offset", type=int, default=0)
    return parser


def _load(root: Path) -> tuple[SearchEngine, int]:
    engine = SearchEngine()
    report = import_directory(engine, root)
    for failure in report.failed:
        print(f"ОТКЛОНЁН {failure.path}: [{failure.code}] {failure.message}", file=sys.stderr)
    return engine, len(report.failed)


def _cmd_check(args: argparse.Namespace) -> int:
    engine, failed = _load(args.root)
    for doc_id in sorted(engine.index.doc_ids()):
        result = analyze(engine.get_document(doc_id))
        print(
            f"{doc_id}: слов {result.words}, заголовков {result.headings}, "
            f"кода {result.code_blocks}, ссылок {result.links}, "
            f"чтение ~{result.reading_minutes} мин"
        )
        for warning in result.warnings:
            print(f"  замечание: {warning}")
    print(f"Принято: {len(engine)}, отклонено: {failed}")
    return 1 if failed else 0


def _cmd_stats(args: argparse.Namespace) -> int:
    engine, failed = _load(args.root)
    size = sum(len(engine.get_document(d).source.encode()) for d in engine.index.doc_ids())
    print(f"Документов: {len(engine)}")
    print(f"Отклонено: {failed}")
    print(f"Терминов в индексе: {engine.index.term_count}")
    print(f"Размер базы: {size} байт")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    engine, _ = _load(args.root)
    query = SearchQuery(
        text=args.query,
        filters=SearchFilters(
            tags=tuple(tag.lower() for tag in args.tag),
            category=args.category,
            author=args.author,
            created_from=args.created_from,
            created_to=args.created_to,
        ),
        scorer=args.scorer,
        sort=args.sort,
        limit=args.limit,
        offset=args.offset,
    )
    result = engine.search(query)
    print(f"Найдено: {result.total} (метод: {args.scorer}, сортировка: {args.sort})")
    for position, hit in enumerate(result.hits, start=query.offset + 1):
        print(f"{position}. [{hit.score:.4f}] {hit.title} ({hit.doc_id})")
        print(f"   теги: {', '.join(hit.tags) or '—'}; категория: {hit.category or '—'}")
        print(f"   {hit.snippet.to_html()}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    # На уровне DEBUG библиотека пишет сообщение на каждое правило разбора и забивает журнал.
    logging.getLogger("markdown_it").setLevel(logging.WARNING)
    handlers = {"check": _cmd_check, "stats": _cmd_stats, "search": _cmd_search}
    try:
        return handlers[args.command](args)
    except KnowledgeBaseError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
