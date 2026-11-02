"""Замер скорости индексации и поиска на синтетическом корпусе документов.

Корпус генерируется с фиксированным зерном: слова берутся из samples, частоты
распределены по закону Ципфа, как в обычных текстах. Результаты воспроизводимы.

    python tools/bench.py --docs 2000
    python tools/bench.py --docs 2000 --profile index
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import random
import re
import statistics
import time
from pathlib import Path

from kb.parser import parse_document
from kb.search import SearchEngine, SearchQuery

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
QUERIES = [
    "индексы для поиска",
    "релевантность документа",
    "кэш базы данных",
    "контейнер запускается",
    "тест проверяет функцию",
    "коммит ошибка история",
    "api documentation",
    "параметр нормализация длина документа",
]


def load_vocabulary() -> list[str]:
    words: set[str] = set()
    for path in SAMPLES.glob("*.md"):
        words.update(re.findall(r"[а-яёa-z]{3,}", path.read_text(encoding="utf-8").lower()))
    vocabulary = sorted(words)
    # Дополнительные формы слов: в настоящих текстах словарь больше, чем в примерах.
    suffixes = ("ами", "ого", "ing")
    return vocabulary + [f"{word}{suffix}" for word in vocabulary for suffix in suffixes]


def generate_corpus(count: int, seed: int = 42) -> list[str]:
    rng = random.Random(seed)
    vocabulary = load_vocabulary()
    rng.shuffle(vocabulary)
    weights = [1 / rank for rank in range(1, len(vocabulary) + 1)]
    tags = ["поиск", "базы", "python", "docker", "кэш", "git", "алгоритмы", "api"]
    documents = []
    for number in range(count):
        size = rng.randint(200, 800)
        words = rng.choices(vocabulary, weights, k=size)
        paragraphs = [" ".join(words[i : i + 60]) for i in range(0, size, 60)]
        heading = " ".join(rng.choices(vocabulary, weights, k=4))
        documents.append(
            f"---\ntitle: Документ {number}\ntags: {rng.sample(tags, 2)}\n"
            f"category: Категория {number % 5}\ncreated: 2026-0{number % 9 + 1}-15\n---\n\n"
            f"# {heading}\n\n" + "\n\n## Раздел\n\n".join(paragraphs)
        )
    return documents


def timed_ms(function, *args) -> float:
    started = time.perf_counter()
    function(*args)
    return (time.perf_counter() - started) * 1000


def measure_search(engine: SearchEngine, scorer: str, rounds: int) -> tuple[float, float]:
    samples = [
        timed_ms(engine.search, SearchQuery(text, scorer=scorer))
        for _ in range(rounds)
        for text in QUERIES
    ]
    ordered = sorted(samples)
    return statistics.median(samples), ordered[int(len(ordered) * 0.95) - 1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=int, default=2000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--profile", choices=["index", "search"])
    args = parser.parse_args()

    corpus = generate_corpus(args.docs)
    megabytes = sum(len(text.encode()) for text in corpus) / 1024 / 1024

    started = time.perf_counter()
    parsed = [parse_document(text) for text in corpus]
    parse_seconds = time.perf_counter() - started

    engine = SearchEngine()

    def index_all() -> None:
        for number, document in enumerate(parsed):
            engine.add_document(f"doc-{number}", document)

    profiler = cProfile.Profile()
    if args.profile == "index":
        profiler.enable()
    started = time.perf_counter()
    index_all()
    index_seconds = time.perf_counter() - started
    if args.profile == "index":
        profiler.disable()

    terms_total = engine.index.term_count
    print(f"Документов: {args.docs}, объём: {megabytes:.1f} МБ, терминов: {terms_total}")
    print(f"Разбор Markdown: {parse_seconds:.2f} с ({args.docs / parse_seconds:.0f} док/с)")
    print(f"Индексация: {index_seconds:.2f} с ({args.docs / index_seconds:.0f} док/с)")

    if args.profile == "search":
        profiler.enable()
    print("Поиск, мс на запрос:")
    for scorer in ("bm25", "tfidf", "hybrid"):
        median, p95 = measure_search(engine, scorer, args.rounds)
        print(f"  {scorer:<7} медиана {median:8.2f}   p95 {p95:8.2f}")
    if args.profile == "search":
        profiler.disable()

    if args.profile:
        print(f"\nПрофиль ({args.profile}), 12 самых затратных функций:")
        stats = pstats.Stats(profiler)
        stats.strip_dirs().sort_stats("tottime").print_stats(12)


if __name__ == "__main__":
    main()
