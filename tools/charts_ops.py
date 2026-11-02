"""Графики эксплуатационных замеров: python tools/charts_ops.py runs/scale.json [runs/load.json].

Читают результаты tools/bench_scale.py и tools/bench_load.py и сохраняют рисунки в docs/img.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "img"

INK = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e4e3df"
# Цвет закреплён за методом и не зависит от того, какие линии есть на рисунке.
SERIES = {"tfidf": "#2a78d6", "bm25": "#eb6834", "hybrid": "#1baf7a", "pgfts": "#eda100"}
NAMES = {"tfidf": "TF-IDF", "bm25": "BM25", "hybrid": "Гибридный", "pgfts": "PostgreSQL FTS"}

plt.rcParams.update(
    {
        "font.family": "Arial",
        "font.size": 12,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": MUTED,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "legend.frameon": False,
    }
)


def _save(fig: plt.Figure, name: str) -> None:
    fig.tight_layout()
    fig.savefig(OUT / name, dpi=200, facecolor="white")
    plt.close(fig)
    print(f"Рисунок: {name}")


def _line(ax: plt.Axes, xs: list[float], ys: list[float], color: str, label: str = "") -> None:
    ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=6, label=label)


def scale_index(core: list[dict]) -> None:
    """Время индексации и память растут вместе с числом слов: два показателя, два графика."""
    words = [row["tokens"] / 1_000_000 for row in core]
    fig, (time_ax, memory_ax) = plt.subplots(1, 2, figsize=(9, 3.8))
    _line(time_ax, words, [row["parse_s"] + row["index_s"] for row in core], SERIES["tfidf"])
    time_ax.set_title("Разбор и индексация, с", loc="left", color=INK, fontsize=12)
    _line(
        memory_ax,
        words,
        [row["rss_after_mb"] - row["rss_before_mb"] for row in core],
        SERIES["tfidf"],
    )
    memory_ax.set_title("Рост памяти процесса, МБ", loc="left", color=INK, fontsize=12)
    for ax in (time_ax, memory_ax):
        ax.set_xlabel("Слов в корпусе, млн")
        ax.set_xlim(left=0)
        ax.set_ylim(bottom=0)
    _save(fig, "scale_index.png")


def scale_search(core: list[dict], database: list[dict]) -> None:
    docs = [row["docs"] for row in core]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for scorer in ("tfidf", "bm25", "hybrid"):
        _line(ax, docs, [row["search_ms"][scorer] for row in core], SERIES[scorer], NAMES[scorer])
    if database:
        _line(
            ax,
            [row["docs"] for row in database],
            [row["pgfts_ms"] for row in database],
            SERIES["pgfts"],
            NAMES["pgfts"],
        )
    ax.set_xlabel("Документов в индексе")
    ax.set_ylabel("Медиана времени запроса, мс")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    _save(fig, "scale_search.png")


def load_rps(results: dict[str, list[dict]]) -> None:
    """Пропускная способность и задержка: разные шкалы, поэтому два отдельных графика."""
    colors = ["#2a78d6", "#eda100", "#1baf7a"]
    fig, (rps_ax, latency_ax) = plt.subplots(1, 2, figsize=(9, 4.2))
    for color, (title, rows) in zip(colors, results.items(), strict=False):
        clients = [row["clients"] for row in rows]
        _line(rps_ax, clients, [row["rps"] for row in rows], color, title)
        _line(latency_ax, clients, [row["p95_ms"] for row in rows], color)
    rps_ax.set_title("Запросов в секунду", loc="left", color=INK, fontsize=12)
    latency_ax.set_title("Время ответа p95, мс", loc="left", color=INK, fontsize=12)
    for ax in (rps_ax, latency_ax):
        ax.set_xlabel("Одновременных клиентов")
        ax.set_xscale("log", base=2)
        ax.set_xticks(clients, [str(value) for value in clients])
        ax.minorticks_off()
        ax.set_ylim(bottom=0)
    fig.legend(loc="upper center", ncol=1, bbox_to_anchor=(0.5, 1.0), fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    fig.savefig(OUT / "load_rps.png", dpi=200, facecolor="white")
    plt.close(fig)
    print("Рисунок: load_rps.png")


def main() -> int:
    scale = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    scale_index(scale["core"])
    scale_search(scale["core"], scale["database"])
    if len(sys.argv) > 2:
        load = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        load_rps(load["results"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
