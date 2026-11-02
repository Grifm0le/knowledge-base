"""Как время и память растут с объёмом данных: python tools/bench_scale.py --sizes 250 500 1000.

Для каждого объёма запускается отдельный процесс, чтобы память не копилась между замерами.
Наклон прямой в логарифмических координатах сравнивается с оценкой сложности алгоритма:
1,0 означает линейный рост, больше 1,0 означает рост быстрее линейного.
Результаты пишутся в JSON: их читает tools/charts_ops.py.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
from bench import QUERIES, generate_corpus
from sqlalchemy import create_engine, text

from kb.cache import MemoryCache, NullCache, SearchCache
from kb.parser import parse_document
from kb.ratelimit import RateLimiter
from kb.search import SearchEngine, SearchQuery
from kb.service import KnowledgeBase
from kb.storage.repository import SqlDocumentRepository
from kb.storage.users import SqlUserRepository
from kb.textproc import terms

SCORERS = ("tfidf", "bm25", "hybrid")


def rss_mb() -> float:
    gc.collect()
    return psutil.Process().memory_info().rss / 1024 / 1024


def one_size(count: int, rounds: int) -> dict[str, object]:
    """Замер одного объёма в чистом процессе: только ядро, без базы."""
    corpus = generate_corpus(count)
    size_mb = sum(len(item.encode("utf-8")) for item in corpus) / 1024 / 1024

    started = time.perf_counter()
    parsed = [parse_document(item) for item in corpus]
    parse_seconds = time.perf_counter() - started
    del corpus

    before = rss_mb()
    engine = SearchEngine()
    started = time.perf_counter()
    for number, document in enumerate(parsed):
        engine.add_document(str(number), document)
    index_seconds = time.perf_counter() - started
    del parsed
    after = rss_mb()

    index = engine.index
    tokens = sum(sum(index.field_lengths(doc_id).values()) for doc_id in index.doc_ids())
    query_terms = [terms(query) for query in QUERIES]
    postings = statistics.mean(
        sum(len(index.postings(term)) for term in set(words)) for words in query_terms
    )

    search_ms: dict[str, float] = {}
    for scorer in SCORERS:
        timings = []
        for _ in range(rounds):
            for query in QUERIES:
                started = time.perf_counter()
                engine.search(SearchQuery(query, scorer=scorer))
                timings.append((time.perf_counter() - started) * 1000)
        search_ms[scorer] = statistics.median(timings)

    return {
        "docs": count,
        "size_mb": round(size_mb, 2),
        "tokens": tokens,
        "unique_terms": index.term_count,
        "postings_per_query": round(postings, 1),
        "parse_s": round(parse_seconds, 3),
        "index_s": round(index_seconds, 3),
        "rss_before_mb": round(before, 1),
        "rss_after_mb": round(after, 1),
        "search_ms": {name: round(value, 3) for name, value in search_ms.items()},
    }


def slope(xs: list[float], ys: list[float]) -> float:
    """Наклон прямой, проведённой методом наименьших квадратов в логарифмических координатах."""
    logs_x = [math.log(x) for x in xs]
    logs_y = [math.log(y) for y in ys]
    mean_x, mean_y = statistics.mean(logs_x), statistics.mean(logs_y)
    top = sum((x - mean_x) * (y - mean_y) for x, y in zip(logs_x, logs_y, strict=True))
    return top / sum((x - mean_x) ** 2 for x in logs_x)


def database_scale(sizes: list[int], rounds: int) -> list[dict[str, object]]:
    """Поиск средствами PostgreSQL и размер базы при тех же объёмах."""
    engine = create_engine(os.environ["KB_BENCH_DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    repository = SqlDocumentRepository(engine)
    users = SqlUserRepository(engine)
    user_id = users.create("bench@example.org", "!", datetime.now(timezone.utc)).id
    writer = KnowledgeBase(
        repository, users, SearchCache(NullCache(), 60), RateLimiter(MemoryCache())
    ).workspace(user_id)

    corpus = generate_corpus(max(sizes))
    results: list[dict[str, object]] = []
    saved = 0
    for count in sizes:
        started = time.perf_counter()
        for document in corpus[saved:count]:
            writer.create_document(document)
        elapsed = time.perf_counter() - started
        added = count - saved
        saved = count
        with engine.begin() as connection:
            connection.execute(text("ANALYZE"))
            database_bytes = connection.scalar(text("SELECT pg_database_size(current_database())"))
            table_bytes = connection.scalar(text("SELECT pg_total_relation_size('documents')"))
            versions_bytes = connection.scalar(
                text("SELECT pg_total_relation_size('document_versions')")
            )
        timings = []
        for _ in range(rounds):
            for query in QUERIES:
                started = time.perf_counter()
                repository.fulltext_scores(user_id, query)
                timings.append((time.perf_counter() - started) * 1000)
        results.append(
            {
                "docs": count,
                "save_ms_per_doc": round(elapsed / added * 1000, 2),
                "pgfts_ms": round(statistics.median(timings), 3),
                "database_mb": round(database_bytes / 1024 / 1024, 1),
                "documents_mb": round(table_bytes / 1024 / 1024, 1),
                "versions_mb": round(versions_bytes / 1024 / 1024, 1),
            }
        )
    engine.dispose()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[250, 500, 1000, 2000, 4000, 8000])
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--one", type=int, help="служебный: замер одного объёма")
    parser.add_argument("--json", type=Path, default=Path("runs/bench_scale.json"))
    args = parser.parse_args()

    if args.one:
        print(json.dumps(one_size(args.one, args.rounds)))
        return 0

    core = []
    for size in args.sizes:
        command = [sys.executable, __file__, "--one", str(size), "--rounds", str(args.rounds)]
        output = subprocess.run(command, capture_output=True, text=True, check=True).stdout  # noqa: S603
        core.append(json.loads(output))

    print("Ядро поиска (процесс Python, без базы)")
    print(
        f"{'докум.':>7} {'МБ':>6} {'слов':>9} {'термов':>7} {'разбор,с':>9} {'индекс,с':>9} "
        f"{'ОЗУ,МБ':>7} {'постинги':>9} {'tfidf,мс':>9} {'bm25,мс':>8} {'hybrid,мс':>10}"
    )
    for row in core:
        memory = row["rss_after_mb"] - row["rss_before_mb"]
        print(
            f"{row['docs']:>7} {row['size_mb']:>6} {row['tokens']:>9} {row['unique_terms']:>7} "
            f"{row['parse_s']:>9} {row['index_s']:>9} {memory:>7.1f} "
            f"{row['postings_per_query']:>9} {row['search_ms']['tfidf']:>9} "
            f"{row['search_ms']['bm25']:>8} {row['search_ms']['hybrid']:>10}"
        )

    tokens = [float(row["tokens"]) for row in core]
    postings = [float(row["postings_per_query"]) for row in core]
    print("\nНаклон в логарифмических координатах (1,0 означает линейный рост)")
    print(f"Индексация от числа слов:       {slope(tokens, [row['index_s'] for row in core]):.2f}")
    memory = [row["rss_after_mb"] - row["rss_before_mb"] for row in core]
    print(f"Память индекса от числа слов:   {slope(tokens, memory):.2f}")
    for scorer in SCORERS:
        value = slope(postings, [row["search_ms"][scorer] for row in core])
        print(f"Поиск {scorer:<6} от числа постингов: {value:.2f}")

    database = []
    if os.environ.get("KB_BENCH_DATABASE_URL"):
        database = database_scale(args.sizes, args.rounds)
        print("\nPostgreSQL")
        print(
            f"{'докум.':>7} {'запись,мс':>10} {'pgfts,мс':>9} {'база,МБ':>8} "
            f"{'докум.,МБ':>10} {'версии,МБ':>10}"
        )
        for row in database:
            print(
                f"{row['docs']:>7} {row['save_ms_per_doc']:>10} {row['pgfts_ms']:>9} "
                f"{row['database_mb']:>8} {row['documents_mb']:>10} {row['versions_mb']:>10}"
            )
        docs = [float(row["docs"]) for row in database]
        pgfts = slope(docs, [row["pgfts_ms"] for row in database])
        size = slope(docs, [row["database_mb"] for row in database])
        print(f"Наклон pgfts от числа документов: {pgfts:.2f}")
        print(f"Наклон размера базы от числа документов: {size:.2f}")

    args.json.write_text(
        json.dumps({"core": core, "database": database}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
