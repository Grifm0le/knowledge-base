"""Кэш на настоящем Redis: сетевой клиент, время жизни, сброс и недоступный сервер."""

import time

import pytest

from fakes import InMemoryRepository, InMemoryUserRepository
from kb.cache import MemoryCache, RedisCache, SearchCache
from kb.ratelimit import Limit, RateLimiter
from kb.search import SearchQuery
from kb.service import KnowledgeBase

pytestmark = pytest.mark.redis

USER = "u1"


def workspace(cache):
    limiter = RateLimiter(MemoryCache())
    base = KnowledgeBase(
        InMemoryRepository(), InMemoryUserRepository(), SearchCache(cache, 60), limiter
    )
    return base.workspace(USER)


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
    service = workspace(RedisCache(live_redis))
    stored = service.create_document("# Заметка\n\nТекст о кэше.")
    query = SearchQuery("кэш")

    assert service.search(query).cache_hit is False
    assert service.search(query).cache_hit is True
    generation = int(live_redis.get(f"kb:search:user:{USER}:generation"))
    assert len(live_redis.keys(f"kb:search:0:{USER}:{generation}:*")) == 1

    service.update_document(stored.id, "# Заметка\n\nНовый текст о кэше.")
    assert int(live_redis.get(f"kb:search:user:{USER}:generation")) == generation + 1
    outcome = service.search(query)
    assert outcome.cache_hit is False
    assert "Новый" in outcome.payload["hits"][0]["snippet"]


def test_refused_connection_is_fast_and_harmless():
    cache = RedisCache.from_url("redis://127.0.0.1:1/0", timeout_seconds=0.25)
    service = workspace(cache)
    service.create_document("# Заметка\n\nТекст о кэше.")

    started = time.perf_counter()
    outcome = service.search(SearchQuery("кэш"))
    elapsed = time.perf_counter() - started

    assert outcome.payload["total"] == 1
    assert outcome.cache_hit is False
    assert cache.ping() is False
    assert elapsed < 5


def test_attempt_counter_lives_in_redis_without_addresses(live_redis):
    limiter = RateLimiter(RedisCache(live_redis))
    limiter.hit("login-email", "anna@example.org", Limit(5, 900))
    keys = live_redis.keys("kb:rate:*")
    assert len(keys) == 1
    assert "anna" not in keys[0]
    assert 0 < live_redis.ttl(keys[0]) <= 900
