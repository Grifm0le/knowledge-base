"""Схемы и графики для документации: python tools/diagrams.py.

Числа для графика берутся из сохранённых замеров в runs/, а не задаются вручную.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
RUNS = ROOT / "runs"

plt.rcParams["font.family"] = "Arial"
plt.rcParams["font.size"] = 13

FILL = {"proc": "#E8F0FE", "start": "#E6F4EA", "error": "#FCE8E6", "cond": "#FEF7E0"}
EDGE = "#3C4043"
INCHES_PER_UNIT = 0.5
MARGIN = 0.3


@dataclass(frozen=True)
class Node:
    key: str
    x: float
    y: float
    text: str
    kind: str = "proc"
    width: float = 5.2
    height: float = 0.95


def _edge_point(node: Node, toward: tuple[float, float]) -> tuple[float, float]:
    """Точка на границе блока по направлению к другой точке."""
    dx, dy = toward[0] - node.x, toward[1] - node.y
    half_w, half_h = node.width / 2, node.height / 2
    if node.kind == "cond":
        scale = 1 / (abs(dx) / half_w + abs(dy) / half_h)
    else:
        scale = min(half_w / abs(dx) if dx else 1e9, half_h / abs(dy) if dy else 1e9)
    return node.x + dx * scale, node.y + dy * scale


def _arrow(ax: plt.Axes, start: tuple[float, float], end: tuple[float, float]) -> None:
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=15,
            color=EDGE,
            linewidth=1.3,
            shrinkA=0,
            shrinkB=0,
        )
    )


def _draw_node(ax: plt.Axes, node: Node) -> None:
    if node.kind == "cond":
        ax.add_patch(
            Polygon(
                [
                    (node.x, node.y + node.height / 2),
                    (node.x + node.width / 2, node.y),
                    (node.x, node.y - node.height / 2),
                    (node.x - node.width / 2, node.y),
                ],
                facecolor=FILL["cond"],
                edgecolor=EDGE,
                linewidth=1.2,
            )
        )
    else:
        ax.add_patch(
            FancyBboxPatch(
                (node.x - node.width / 2, node.y - node.height / 2),
                node.width,
                node.height,
                boxstyle="round,pad=0.02,rounding_size=0.12",
                facecolor=FILL[node.kind],
                edgecolor=EDGE,
                linewidth=1.2,
            )
        )
    ax.text(node.x, node.y, node.text, ha="center", va="center")


def _draw(nodes: list[Node], edges: list[tuple], name: str, polylines: tuple = ()) -> None:
    by_key = {node.key: node for node in nodes}
    line_xs = [x for points, _ in polylines for x, _ in points]
    line_ys = [y for points, _ in polylines for _, y in points]
    left = min([n.x - n.width / 2 for n in nodes] + line_xs) - MARGIN
    right = max([n.x + n.width / 2 for n in nodes] + line_xs) + MARGIN
    bottom = min([n.y - n.height / 2 for n in nodes] + line_ys) - MARGIN
    top = max([n.y + n.height / 2 for n in nodes] + line_ys) + MARGIN

    fig = plt.figure(figsize=((right - left) * INCHES_PER_UNIT, (top - bottom) * INCHES_PER_UNIT))
    ax = fig.add_axes((0, 0, 1, 1))
    for node in nodes:
        _draw_node(ax, node)

    for edge in edges:
        source, target, label = (*edge, None)[:3]
        a, b = by_key[source], by_key[target]
        start, end = _edge_point(a, (b.x, b.y)), _edge_point(b, (a.x, a.y))
        _arrow(ax, start, end)
        if label:
            middle = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
            vertical = abs(a.x - b.x) < 0.01
            ax.text(
                middle[0] + (0.14 if vertical else 0),
                middle[1] + (0 if vertical else 0.1),
                label,
                fontsize=12,
                ha="left" if vertical else "center",
                va="center" if vertical else "bottom",
            )

    for points, label in polylines:
        xs, ys = zip(*points[:-1], strict=True)
        ax.plot(xs, ys, color=EDGE, linewidth=1.3, solid_capstyle="round")
        _arrow(ax, points[-2], points[-1])
        if label:
            ax.text(
                points[0][0] - 0.25, points[0][1] + 0.1, label, fontsize=12, ha="right", va="bottom"
            )

    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.set_aspect("equal")
    ax.axis("off")
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=200, facecolor="white")
    plt.close(fig)


def modules_diagram() -> None:
    w, h = 6.2, 1.25
    nodes = [
        Node("cli", 7.2, 12.6, "cli: команды check, stats,\nsearch", "start", w, h),
        Node(
            "analysis", 0, 10.4, "analysis: показатели,\nоглавление, замечания", width=w, height=h
        ),
        Node(
            "importer", 7.2, 10.4, "importer: обход каталога,\nотчёт об импорте", width=w, height=h
        ),
        Node(
            "search", 14.4, 10.4, "search: поиск, фильтры,\nсортировка, страницы", width=w, height=h
        ),
        Node(
            "parser",
            0,
            8.2,
            "parser: YAML-шапка, заголовки,\nкод, ссылки, текст",
            width=w,
            height=h,
        ),
        Node(
            "scoring", 7.2, 8.2, "scoring: TF-IDF, BM25,\nгибрид, реестр методов", width=w, height=h
        ),
        Node("highlight", 14.4, 8.2, "highlight: фрагмент\nс подсветкой", width=w, height=h),
        Node(
            "normalize",
            0,
            6.0,
            "normalize: кодировка, размер,\nтекст и метаданные",
            width=w,
            height=h,
        ),
        Node("index", 7.2, 6.0, "index: обратный индекс,\nдлины полей", width=w, height=h),
        Node("textproc", 14.4, 6.0, "textproc: слова, стоп-слова,\nосновы", width=w, height=h),
    ]
    edges = [
        ("cli", "analysis"),
        ("cli", "importer"),
        ("cli", "search"),
        ("analysis", "parser"),
        ("importer", "parser"),
        ("importer", "search"),
        ("parser", "normalize"),
        ("search", "scoring"),
        ("search", "highlight"),
        ("scoring", "index"),
        ("index", "textproc"),
        ("highlight", "textproc"),
    ]
    _draw(nodes, edges, "modules.png")


def import_flow() -> None:
    w = 6.4
    nodes = [
        Node("start", 3, 17, "Начало: каталог с документами", "start", w),
        Node("next", 3, 15.6, "Взять следующий файл *.md", width=w),
        Node(
            "read",
            3,
            14.2,
            "Прочитать байты, проверить размер\nи кодировку UTF-8",
            width=w,
            height=1.1,
        ),
        Node(
            "norm",
            3,
            12.8,
            "Нормализовать текст: NFC, CRLF,\nневидимые символы, пробелы",
            width=w,
            height=1.1,
        ),
        Node("front", 3, 11.4, "Отделить YAML-шапку и\nпроверить типы полей", width=w, height=1.1),
        Node(
            "parse",
            3,
            10,
            "Разобрать Markdown: заголовки,\nкод, ссылки, текст",
            width=w,
            height=1.1,
        ),
        Node("valid", 3, 8.0, "Документ\nпринят?", "cond", 4.2, 1.9),
        Node(
            "index",
            3,
            5.9,
            "Разбить поля на термины и\nдобавить в обратный индекс",
            width=w,
            height=1.1,
        ),
        Node("more", 3, 3.9, "Файлы\nещё есть?", "cond", 4.2, 1.9),
        Node("end", 3, 1.9, "Конец: отчёт об импорте", "start", w),
        Node("fail", 9.3, 8.0, "Записать код ошибки\nв отчёт, продолжить", "error", 4.4, 1.1),
    ]
    edges = [
        ("start", "next"),
        ("next", "read"),
        ("read", "norm"),
        ("norm", "front"),
        ("front", "parse"),
        ("parse", "valid"),
        ("valid", "index", "да"),
        ("index", "more"),
        ("more", "end", "нет"),
        ("valid", "fail", "нет"),
    ]
    polylines = (
        (((0.9, 3.9), (-1.0, 3.9), (-1.0, 15.6), (-0.2, 15.6)), "да"),
        (((9.3, 7.45), (9.3, 3.9), (5.1, 3.9)), None),
    )
    _draw(nodes, edges, "import_flow.png", polylines)


def search_flow() -> None:
    w = 6.4
    nodes = [
        Node(
            "start",
            3,
            15.6,
            "Запрос: текст, фильтры, метод,\nсортировка, страница",
            "start",
            w,
            1.1,
        ),
        Node("check", 3, 13.7, "Параметры\nкорректны?", "cond", 4.2, 1.9),
        Node(
            "terms",
            3,
            11.6,
            "Текст запроса → термины\n(регистр, «ё», стоп-слова, основа)",
            width=w,
            height=1.1,
        ),
        Node(
            "score",
            3,
            10.1,
            "Для каждого термина обойти список документов\nи накопить оценку выбранным методом",
            width=w + 2.2,
            height=1.1,
        ),
        Node("filter", 3, 8.6, "Оставить документы, подходящие\nпод фильтры", width=w, height=1.1),
        Node(
            "sort", 3, 7.1, "Отсортировать: релевантность,\nдата или название", width=w, height=1.1
        ),
        Node("page", 3, 5.75, "Выбрать страницу (limit, offset)", width=w),
        Node(
            "snip",
            3,
            4.4,
            "Построить фрагмент с подсветкой\nдля каждого результата",
            width=w,
            height=1.1,
        ),
        Node("end", 3, 3.05, "Результаты и общее число найденных", "start", w + 0.6),
        Node("err", 9.2, 13.7, "Ошибка запроса", "error", 3.2),
    ]
    edges = [
        ("start", "check"),
        ("check", "terms", "да"),
        ("terms", "score"),
        ("score", "filter"),
        ("filter", "sort"),
        ("sort", "page"),
        ("page", "snip"),
        ("snip", "end"),
        ("check", "err", "нет"),
    ]
    _draw(nodes, edges, "search_flow.png")


def _read_bench(name: str) -> dict[str, float]:
    text = (RUNS / name).read_text(encoding="utf-8")
    values = {"index": float(re.search(r"Индексация: ([\d.]+) с", text).group(1))}
    for method in ("bm25", "tfidf", "hybrid"):
        values[method] = float(re.search(rf"{method}\s+медиана\s+([\d.]+)", text).group(1))
    return values


def _comma(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def bench_chart() -> None:
    stages = [
        ("Исходная\nверсия", "11_bench_before.txt"),
        ("Кэш\nстемминга", "13_bench_step1_stem_cache.txt"),
        ("Устранение\nлишних вычислений", "20_bench_final.txt"),
    ]
    data = [(label, _read_bench(name)) for label, name in stages]
    fig, (left, right) = plt.subplots(1, 2, figsize=(9.6, 4.4))
    colors = ["#9AA0A6", "#4285F4", "#34A853"]

    labels = [label for label, _ in data]
    values = [item["index"] for _, item in data]
    bars = left.bar(labels, values, color=colors)
    left.bar_label(bars, labels=[f"{_comma(v, 2)} с" for v in values], padding=3)
    left.set_title("Индексация 2000 документов")
    left.set_ylabel("секунды")
    left.margins(y=0.15)
    left.tick_params(axis="x", labelsize=11)

    methods = ["bm25", "tfidf", "hybrid"]
    width = 0.28
    for number, (label, item) in enumerate(data):
        positions = [i + (number - 1) * width for i in range(len(methods))]
        heights = [item[m] for m in methods]
        bars = right.bar(
            positions, heights, width, label=label.replace("\n", " "), color=colors[number]
        )
        right.bar_label(bars, labels=[_comma(h, 1) for h in heights], padding=2, fontsize=10)
    right.set_xticks(range(len(methods)), ["BM25", "TF-IDF", "Гибрид"])
    right.set_title("Поиск: медиана времени запроса")
    right.set_ylabel("миллисекунды")
    right.set_ylim(0, 190)
    right.legend(fontsize=11, frameon=False, loc="upper center")

    for axis in (left, right):
        axis.spines[["top", "right"]].set_visible(False)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT / "bench.png", dpi=200, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    modules_diagram()
    import_flow()
    search_flow()
    bench_chart()
    print("Сохранено:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))
