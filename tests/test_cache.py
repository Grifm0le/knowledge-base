"""Кэш: реализации интерфейса, ключи запросов и поведение при сбое Redis."""

import logging

import fakeredis
import pytest

from fakes import BrokenRedis
from kb.cache import GENERATION_KEY, MemoryCache, NullCache, RedisCache, SearchCache


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(params=["memory", "redis"])
def cache(request):
    """Одни и те же проверки для обеих реализаций."""
    if request.param == "memory":
        return MemoryCache()
    return RedisCache(fakeredis.FakeRedis(decode_responses=True))


def test_missing_key_gives_none(cache):
    assert cache.get("нет") is None


def test_value_is_stored_with_unicode(cache):
    cache.set("ключ", "значение", 60)
    assert cache.get("ключ") == "значение"


def test_increment_starts_from_one(cache):
    cache.increment("счётчик")
    cache.increment("счётчик")
    assert cache.get("счётчик") == "2"


def test_ping(cache):
    assert cache.ping() is True


def test_memory_cache_forgets_value_after_ttl():
    clock = Clock()
    cache = MemoryCache(clock)
    cache.set("k", "v", 10)
    clock.now += 9
    assert cache.get("k") == "v"
    clock.now += 1
    assert cache.get("k") is None


def test_redis_cache_sets_ttl():
    client = fakeredis.FakeRedis(decode_responses=True)
    RedisCache(client).set("k", "v", 60)
    assert 0 < client.ttl("k") <= 60


def test_null_cache_stores_nothing():
    cache = NullCache()
    cache.set("k", "v", 60)
    cache.increment("k")
    assert cache.get("k") is None
    assert cache.ping() is True


def test_broken_redis_behaves_as_empty_cache(caplog):
    cache = RedisCache(BrokenRedis())
    with caplog.at_level(logging.WARNING, logger="kb.cache"):
        assert cache.get("k") is None
        cache.set("k", "v", 60)
        cache.increment("k")
    assert cache.ping() is False
    assert len(caplog.records) == 3
    assert all("Redis недоступен" in record.getMessage() for record in caplog.records)


def test_same_query_gives_same_key_regardless_of_order():
    search_cache = SearchCache(MemoryCache(), 60)
    first = search_cache.key_for({"text": "кэш", "limit": 10})
    second = search_cache.key_for({"limit": 10, "text": "кэш"})
    assert first == second
    assert first.startswith("kb:search:0:")


def test_different_queries_give_different_keys():
    search_cache = SearchCache(MemoryCache(), 60)
    assert search_cache.key_for({"text": "кэш"}) != search_cache.key_for({"text": "кеш"})


def test_payload_round_trip_counts_hits_and_misses():
    search_cache = SearchCache(MemoryCache(), 60)
    key = search_cache.key_for({"text": "кэш"})
    assert search_cache.get(key) is None
    search_cache.put(key, {"total": 1, "title": "Кэш"})
    assert search_cache.get(key) == {"total": 1, "title": "Кэш"}
    assert (search_cache.hits, search_cache.misses) == (1, 1)


def test_invalidate_changes_generation_and_hides_old_entries():
    storage = MemoryCache()
    search_cache = SearchCache(storage, 60)
    old_key = search_cache.key_for({"text": "кэш"})
    search_cache.put(old_key, {"total": 1})
    search_cache.invalidate()
    new_key = search_cache.key_for({"text": "кэш"})
    assert new_key != old_key
    assert search_cache.get(new_key) is None
    assert storage.get(GENERATION_KEY) == "1"
