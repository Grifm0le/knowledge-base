"""Кэш на настоящем Redis: сетевой клиент, время жизни, сброс и недоступный сервер."""

import time

import pytest

from fakes import InMemoryRepository
from kb.cache import GENERATION_KEY, RedisCache, SearchCache
from kb.search import SearchEngine, SearchQuery
from kb.service import KnowledgeBase

pytestmark = pytest.mark.redis


def test_value_and_ttl_on_real_server(live_redis):
    cache = RedisCache(live_redis)
    cache.set("kb:test:ключ", "значение", 60)
    assert cache.get("kb:test:ключ") == "значение"
    assert 0 < live_redis.ttl("kb:test:ключ") <= 60
    assert cache.ping() is True


def test_value_really_expires(live_redis):
    cache = RedisCache(live_redis)
    cache.set("kb:test:короткий", "значение", 1)
    time.sleep(1.2)
    assert cache.get("kb:test:короткий") is None


def test_service_caches_and_resets_on_real_server(live_redis):
    search_cache = SearchCache(RedisCache(live_redis), 60)
    service = KnowledgeBase(InMemoryRepository(), SearchEngine(), search_cache)
    stored = service.create_document("# Заметка\n\nТекст о кэше.")
    query = SearchQuery("кэш")

    assert service.search(query).cache_hit is False
    assert service.search(query).cache_hit is True
    generation = int(live_redis.get(GENERATION_KEY))
    assert len(live_redis.keys(f"kb:search:{generation}:*")) == 1

    service.update_document(stored.id, "# Заметка\n\nНовый текст о кэше.")
    assert int(live_redis.get(GENERATION_KEY)) == generation + 1
    outcome = service.search(query)
    assert outcome.cache_hit is False
    assert "Новый" in outcome.payload["hits"][0]["snippet"]


def test_refused_connection_is_fast_and_harmless():
    cache = RedisCache.from_url("redis://127.0.0.1:1/0", timeout_seconds=0.25)
    service = KnowledgeBase(InMemoryRepository(), SearchEngine(), SearchCache(cache, 60))
    service.create_document("# Заметка\n\nТекст о кэше.")

    started = time.perf_counter()
    outcome = service.search(SearchQuery("кэш"))
    elapsed = time.perf_counter() - started

    assert outcome.payload["total"] == 1
    assert outcome.cache_hit is False
    assert cache.ping() is False
    assert elapsed < 5
