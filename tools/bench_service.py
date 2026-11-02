"""Замер сервиса на настоящих PostgreSQL и Redis: python tools/bench_service.py --docs 2000.

Использует корпус из tools/bench.py и отдельную базу KB_BENCH_DATABASE_URL, которую очищает.
Замеряет сохранение, построение индекса при запуске, поиск разными методами и эффект кэша.
"""

from __future__ import annotations

import argparse
import os
import statistics
import time
from datetime import datetime, timezone

import redis
from bench import QUERIES, generate_corpus
from sqlalchemy import create_engine, text

from kb.cache import MemoryCache, NullCache, RedisCache, SearchCache
from kb.ratelimit import RateLimiter
from kb.search import SearchQuery
from kb.service import KnowledgeBase
from kb.storage.repository import SqlDocumentRepository
from kb.storage.users import SqlUserRepository

QUERY_SQL = "replace(websearch_to_tsquery('russian', :text)::text, ' & ', ' | ')::tsquery"
FULLTEXT_SQL = f"""
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT id, ts_rank_cd(search_vector, {QUERY_SQL}) FROM documents
WHERE search_vector @@ {QUERY_SQL}
"""


def timed_search(service: KnowledgeBase, scorer: str, rounds: int) -> list[float]:
    timings = []
    for _ in range(rounds):
        for text_ in QUERIES:
            started = time.perf_counter()
            service.search(SearchQuery(text_, scorer=scorer))
            timings.append((time.perf_counter() - started) * 1000)
    return timings


def line(name: str, timings: list[float]) -> str:
    timings = sorted(timings)
    p95 = timings[int(len(timings) * 0.95) - 1]
    return f"  {name:<26} медиана {statistics.median(timings):8.2f}   p95 {p95:8.2f}"


def explain(engine, title: str, query_text: str, *setup: str) -> None:
    """Показывает план запроса. Изменения схемы откатываются вместе с транзакцией."""
    print(f"\n{title}")
    with engine.connect() as connection:
        for statement in setup:
            connection.execute(text(statement))
        plan = connection.execute(text(FULLTEXT_SQL), {"text": query_text}).scalars().all()
        connection.rollback()
    print("\n".join(f"  {row}" for row in plan))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=int, default=2000)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()

    engine = create_engine(os.environ["KB_BENCH_DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    repository = SqlDocumentRepository(engine)
    users = SqlUserRepository(engine)
    user_id = users.create("bench@example.org", "!", datetime.now(timezone.utc)).id
    limiter = RateLimiter(MemoryCache())
    writer = KnowledgeBase(repository, users, SearchCache(MemoryCache(), 60), limiter).workspace(
        user_id
    )

    corpus = generate_corpus(args.docs)
    started = time.perf_counter()
    for document in corpus:
        writer.create_document(document)
    elapsed = time.perf_counter() - started
    size = sum(len(document.encode("utf-8")) for document in corpus)
    print(f"Документов: {len(corpus)}, объём: {size / 1024 / 1024:.1f} МБ")
    print(f"Сохранение в базу и индекс: {elapsed:.2f} с ({len(corpus) / elapsed:.0f} док/с)")

    base = KnowledgeBase(repository, users, SearchCache(NullCache(), 60), limiter)
    started = time.perf_counter()
    base.load_index()
    service = base.workspace(user_id)
    print(f"Построение индекса при запуске: {time.perf_counter() - started:.2f} с")

    print("\nПоиск без кэша, мс на запрос:")
    for scorer in ("bm25", "tfidf", "hybrid", "pgfts"):
        print(line(scorer, timed_search(service, scorer, args.rounds)))

    url = os.environ.get("KB_BENCH_REDIS_URL")
    if url:
        client = redis.Redis.from_url(url, decode_responses=True)
        client.flushdb()
        cached_base = KnowledgeBase(
            repository, users, SearchCache(RedisCache(client), 300), limiter
        )
        cached_base.load_index()
        cached = cached_base.workspace(user_id)
        print("\nПоиск через Redis, мс на запрос:")
        print(line("bm25, промах кэша", timed_search(cached, "bm25", 1)))
        print(line("bm25, попадание в кэш", timed_search(cached, "bm25", args.rounds)))
        memory = client.info("memory")["used_memory_human"]
        print(f"  записей в Redis: {client.dbsize()}, память: {memory}")
        client.flushdb()

    writer.create_document("# Редкая заметка\n\nСлово колоссальный встречается только здесь.")
    with engine.begin() as connection:
        connection.execute(text("ANALYZE documents"))
    drop = "DROP INDEX ix_documents_search_vector"
    explain(engine, "Частые слова, есть индекс GIN:", QUERIES[0])
    explain(engine, "Редкое слово, есть индекс GIN:", "колоссальный")
    explain(engine, "Редкое слово, индекс удалён (внутри транзакции):", "колоссальный", drop)


if __name__ == "__main__":
    main()
