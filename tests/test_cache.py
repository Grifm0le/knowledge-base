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
    first = search_cache.key_for("u1", {"text": "кэш", "limit": 10})
    second = search_cache.key_for("u1", {"limit": 10, "text": "кэш"})
    assert first == second
    assert first.startswith("kb:search:0:u1:0:")


def test_different_queries_give_different_keys():
    search_cache = SearchCache(MemoryCache(), 60)
    assert search_cache.key_for("u1", {"text": "кэш"}) != search_cache.key_for(
        "u1", {"text": "кеш"}
    )


def test_same_query_of_different_users_gives_different_keys():
    search_cache = SearchCache(MemoryCache(), 60)
    assert search_cache.key_for("u1", {"text": "кэш"}) != search_cache.key_for(
        "u2", {"text": "кэш"}
    )


def test_payload_round_trip_counts_hits_and_misses():
    search_cache = SearchCache(MemoryCache(), 60)
    key = search_cache.key_for("u1", {"text": "кэш"})
    assert search_cache.get(key) is None
    search_cache.put(key, {"total": 1, "title": "Кэш"})
    assert search_cache.get(key) == {"total": 1, "title": "Кэш"}
    assert (search_cache.hits, search_cache.misses) == (1, 1)


def test_invalidate_hides_entries_of_this_user_only():
    search_cache = SearchCache(MemoryCache(), 60)
    mine = search_cache.key_for("u1", {"text": "кэш"})
    theirs = search_cache.key_for("u2", {"text": "кэш"})
    search_cache.put(mine, {"total": 1})
    search_cache.put(theirs, {"total": 2})
    search_cache.invalidate("u1")
    assert search_cache.key_for("u1", {"text": "кэш"}) != mine
    assert search_cache.key_for("u2", {"text": "кэш"}) == theirs


def test_invalidate_all_changes_common_generation():
    storage = MemoryCache()
    search_cache = SearchCache(storage, 60)
    old_key = search_cache.key_for("u1", {"text": "кэш"})
    search_cache.invalidate_all()
    assert search_cache.key_for("u1", {"text": "кэш"}) != old_key
    assert storage.get(GENERATION_KEY) == "1"


def test_count_hit_counts_and_can_be_reset(cache):
    assert cache.count_hit("попытки", 60) == 1
    assert cache.count_hit("попытки", 60) == 2
    cache.delete("попытки")
    assert cache.get("попытки") is None
    assert cache.count_hit("попытки", 60) == 1


def test_memory_count_hit_expires():
    clock = Clock()
    cache = MemoryCache(clock)
    cache.count_hit("попытки", 10)
    cache.count_hit("попытки", 10)
    clock.now += 10
    assert cache.count_hit("попытки", 10) == 1


def test_redis_count_hit_sets_ttl_once():
    client = fakeredis.FakeRedis(decode_responses=True)
    cache = RedisCache(client)
    cache.count_hit("попытки", 60)
    client.expire("попытки", 30)
    cache.count_hit("попытки", 60)
    assert 0 < client.ttl("попытки") <= 30


def test_broken_redis_does_not_count_attempts():
    cache = RedisCache(BrokenRedis())
    assert cache.count_hit("попытки", 60) == 0
    cache.delete("попытки")


def test_null_cache_does_not_count():
    cache = NullCache()
    assert cache.count_hit("попытки", 60) == 0
    cache.delete("попытки")
