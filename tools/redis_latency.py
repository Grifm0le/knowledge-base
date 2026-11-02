"""Задержка записи и чтения в Redis при разном размере значения.

Запускается внутри контейнера сервиса, где есть адрес Redis:
    docker compose exec -T app python - < tools/redis_latency.py
"""

from __future__ import annotations

import os
import statistics
import time

import redis

ROUNDS = 200
SIZES = (100, 1000, 3000, 5000, 5900, 6100, 8000, 20000)


def median_ms(action, rounds: int = ROUNDS) -> float:  # type: ignore[no-untyped-def]
    timings = []
    for _ in range(rounds):
        started = time.perf_counter()
        action()
        timings.append((time.perf_counter() - started) * 1000)
    return statistics.median(timings)


def main() -> None:
    client = redis.Redis.from_url(os.environ["KB_REDIS_URL"])
    print(f"redis-py {redis.__version__}, сервер Redis {client.info('server')['redis_version']}")
    for size in SIZES:
        value = "x" * size
        key = f"kb:latency:{size}"
        write = median_ms(lambda key=key, value=value: client.set(key, value, ex=60))
        read = median_ms(lambda key=key: client.get(key))
        print(f"значение {size:>6} байт   SET медиана {write:7.2f} мс   GET медиана {read:7.2f} мс")
        client.delete(key)


if __name__ == "__main__":
    main()
