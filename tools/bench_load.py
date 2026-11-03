"""Нагрузка на развёрнутый сервис: python tools/bench_load.py [адрес] [адрес Mailpit] --docs 2000.

Создаёт пользователя, загружает корпус и выполняет поиск с разным числом одновременных клиентов.
Параллельно раз в секунду читает docker stats: загрузку процессора и память каждого контейнера.
Результаты пишутся в JSON: их читает tools/charts_ops.py.
"""

from __future__ import annotations

import argparse
import json
import random
import shlex
import statistics
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bench import QUERIES, generate_corpus
from stack_client import API, Mailbox, make_client, new_user

PASSWORD = "надёжная-фраза-для-проверки"
BATCH = 200


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * share))]


class DockerStats(threading.Thread):
    """Снимает docker stats, пока идёт замер."""

    def __init__(self, command: str) -> None:
        super().__init__(daemon=True)
        self._command = shlex.split(command)
        self._finished = threading.Event()
        self.samples: dict[str, list[tuple[float, float]]] = {}

    def run(self) -> None:
        while not self._finished.is_set():
            for name, cpu, memory in read_stats(self._command):
                self.samples.setdefault(name, []).append((cpu, memory))
            self._finished.wait(1)

    def stop(self) -> dict[str, dict[str, float]]:
        self._finished.set()
        self.join(timeout=30)
        return {
            name: {
                "cpu_mean": round(statistics.mean(s[0] for s in samples), 1),
                "cpu_max": round(max(s[0] for s in samples), 1),
                "memory_max_mb": round(max(s[1] for s in samples), 1),
            }
            for name, samples in self.samples.items()
        }


def read_stats(command: list[str]) -> list[tuple[str, float, float]]:
    output = subprocess.run(  # noqa: S603
        [*command, "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}};{{.MemUsage}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows = []
    for line in output.strip().splitlines():
        name, cpu, memory = line.split(";")
        rows.append((name, float(cpu.rstrip("%")), to_mb(memory.split("/")[0].strip())))
    return rows


def to_mb(value: str) -> float:
    for unit, factor in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / 1024 / 1024)):
        if value.endswith(unit):
            return float(value[: -len(unit)]) * factor
    raise ValueError(value)


def upload(base_url: str, token: str, corpus: list[str]) -> float:
    started = time.perf_counter()
    with make_client(base_url, token) as client:
        for start in range(0, len(corpus), BATCH):
            files = [
                ("files", (f"doc{start + i}.md", item.encode("utf-8"), "text/markdown"))
                for i, item in enumerate(corpus[start : start + BATCH])
            ]
            response = client.post(f"{API}/documents/import", files=files)
            response.raise_for_status()
            assert not response.json()["failed"]
    return time.perf_counter() - started


def run_clients(
    base_url: str, token: str, clients: int, seconds: float, scorer: str, unique: bool
) -> dict[str, float]:
    """Клиенты выполняют запросы подряд. Если unique, каждый запрос новый и минует кэш."""
    stop_at = time.monotonic() + seconds

    def worker(number: int) -> list[float]:
        rng = random.Random(number)
        timings = []
        with make_client(base_url, token) as client:
            while time.monotonic() < stop_at:
                query = rng.choice(QUERIES)
                params: dict[str, object] = {"q": query, "scorer": scorer}
                if unique:
                    params["offset"] = 0
                    params["w_title"] = round(rng.uniform(0.1, 99), 4)
                started = time.perf_counter()
                response = client.get(f"{API}/search", params=params)
                timings.append((time.perf_counter() - started) * 1000)
                response.raise_for_status()
        return timings

    started = time.perf_counter()
    with ThreadPoolExecutor(clients) as pool:
        timings = [item for part in pool.map(worker, range(clients)) for item in part]
    elapsed = time.perf_counter() - started
    return {
        "clients": clients,
        "requests": len(timings),
        "rps": round(len(timings) / elapsed, 1),
        "p50_ms": round(percentile(timings, 0.5), 1),
        "p95_ms": round(percentile(timings, 0.95), 1),
    }


def write_latency(base_url: str, token: str, rounds: int) -> dict[str, float]:
    """Время сохранения новой версии: при каждой записи сбрасывается поколение кэша в Redis."""
    timings = []
    with make_client(base_url, token) as client:
        created = client.post(f"{API}/documents", json={"content": "# Замер\n\nТекст 0.\n"}).json()
        url = f"{API}/documents/{created['id']}"
        for number in range(1, rounds + 1):
            started = time.perf_counter()
            response = client.put(url, json={"content": f"# Замер\n\nТекст {number}.\n"})
            timings.append((time.perf_counter() - started) * 1000)
            response.raise_for_status()
    return {
        "rounds": rounds,
        "p50_ms": round(percentile(timings, 0.5), 1),
        "p95_ms": round(percentile(timings, 0.95), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", nargs="?", default="https://127.0.0.1:8443")
    parser.add_argument("mailpit", nargs="?", default="http://127.0.0.1:8025")
    parser.add_argument("--docs", type=int, default=2000)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--clients", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    parser.add_argument("--docker", default="docker", help="команда запуска docker")
    parser.add_argument("--json", type=Path, default=Path("runs/bench_load.json"))
    parser.add_argument("--write-only", action="store_true", help="только время сохранения")
    args = parser.parse_args()

    email = f"load-{uuid.uuid4().hex[:8]}@example.org"
    token = new_user(args.base, Mailbox(args.mailpit), email, PASSWORD)
    if args.write_only:
        write = write_latency(args.base, token, 100)
        print(f"Сохранение новой версии: p50 {write['p50_ms']} мс, p95 {write['p95_ms']} мс")
        return 0
    command = shlex.split(args.docker)

    idle = read_stats(command)
    print("Контейнеры без нагрузки и без документов")
    for name, cpu, memory in idle:
        print(f"  {name:<32} ЦП {cpu:>6.2f} %  ОЗУ {memory:>8.1f} МБ")

    corpus = generate_corpus(args.docs)
    size = sum(len(item.encode("utf-8")) for item in corpus) / 1024 / 1024
    stats = DockerStats(args.docker)
    stats.start()
    upload_seconds = upload(args.base, token, corpus)
    upload_stats = stats.stop()
    print(f"\nЗагружено документов: {args.docs}, {size:.1f} МБ, за {upload_seconds:.1f} с")
    print(f"Скорость: {args.docs / upload_seconds:.1f} документов в секунду")

    results: dict[str, list[dict[str, float]]] = {}
    load_stats: dict[str, dict[str, dict[str, float]]] = {}
    modes = (
        ("bm25, новые запросы (минуя кэш)", "bm25", True),
        ("pgfts, новые запросы (минуя кэш)", "pgfts", True),
        ("bm25, повторные запросы (из кэша)", "bm25", False),
    )
    for title, scorer, unique in modes:
        print(f"\n{title}")
        print(f"{'клиентов':>9} {'запросов':>9} {'запр./с':>8} {'p50, мс':>8} {'p95, мс':>8}")
        results[title] = []
        stats = DockerStats(args.docker)
        stats.start()
        for clients in args.clients:
            row = run_clients(args.base, token, clients, args.seconds, scorer, unique)
            results[title].append(row)
            print(
                f"{row['clients']:>9} {row['requests']:>9} {row['rps']:>8} "
                f"{row['p50_ms']:>8} {row['p95_ms']:>8}"
            )
        load_stats[title] = stats.stop()

    print("\nКонтейнеры под нагрузкой: 100 % означает одно занятое ядро")
    print(f"{'режим':<36} {'контейнер':<30} {'ЦП сред.':>9} {'ЦП макс.':>9} {'ОЗУ макс.':>10}")
    for title, per_container in {"загрузка документов": upload_stats, **load_stats}.items():
        for name, values in sorted(per_container.items()):
            print(
                f"{title:<36} {name:<30} {values['cpu_mean']:>9} {values['cpu_max']:>9} "
                f"{values['memory_max_mb']:>10}"
            )

    write = write_latency(args.base, token, 100)
    print(f"\nСохранение новой версии: p50 {write['p50_ms']} мс, p95 {write['p95_ms']} мс")

    args.json.write_text(
        json.dumps(
            {
                "docs": args.docs,
                "size_mb": round(size, 1),
                "upload_seconds": round(upload_seconds, 1),
                "idle": [{"name": n, "cpu": c, "memory_mb": m} for n, c, m in idle],
                "upload_stats": upload_stats,
                "results": results,
                "load_stats": load_stats,
                "write": write,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
