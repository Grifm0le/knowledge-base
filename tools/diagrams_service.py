"""Схемы сервиса: python tools/diagrams_service.py.

Диаграммы классов и таблиц строятся разбором исходного кода, поэтому не расходятся с ним.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
from diagrams import EDGE, FILL, INCHES_PER_UNIT, OUT, ROOT, Node, _draw
from matplotlib.patches import FancyArrowPatch, Rectangle
from sqlalchemy.dialects import postgresql

from kb.storage.tables import Base

SRC = ROOT / "src" / "kb"
FILL.update({"ext": "#F3E8FD"})
MEMBER_FONT = 14
LINE = 0.56


def architecture() -> None:
    w = 4.6
    y = 5.6
    nodes = [
        Node(
            "client", 9.8, 11.6, "Клиенты: Swagger UI, curl, tools/api_smoke.py", "ext", 19.4, 0.95
        ),
        Node(
            "api",
            9.8,
            9.8,
            "REST API, пакет kb.api: методы, схемы, ошибки, журнал запросов (FastAPI, uvicorn)",
            width=19.4,
            height=0.95,
        ),
        Node(
            "service",
            9.8,
            8.0,
            "Сервис KnowledgeBase: сохранение, индекс, кэш, версии, импорт",
            width=19.4,
            height=0.95,
        ),
        Node("parse", 2.3, y, "Разбор документа\nnormalize, parser,\nmodels", width=w, height=1.9),
        Node(
            "core",
            7.2,
            y,
            "Поисковое ядро\nSearchEngine, index,\nscoring, highlight,\ntextproc",
            width=w,
            height=1.9,
        ),
        Node("cache", 12.1, y, "Кэш выдачи\nSearchCache, Cache,\nRedisCache", width=w, height=1.9),
        Node(
            "repo",
            17.0,
            y,
            "Хранилище\nDocumentRepository,\nSqlDocumentRepository",
            width=w,
            height=1.9,
        ),
        Node("memory", 7.2, 2.5, "Обратный индекс\nв памяти процесса", width=w, height=1.3),
        Node("redis", 12.1, 2.5, "Redis\nзаписи с TTL и\nномер поколения", "ext", w, 1.5),
        Node("pg", 17.0, 2.5, "PostgreSQL\ndocuments,\ndocument_versions", "ext", w, 1.5),
    ]
    edges = [
        ("client", "api", "HTTP, JSON"),
        ("api", "service"),
        ("core", "memory"),
        ("cache", "redis", "RESP"),
        ("repo", "pg", "SQL"),
    ]
    polylines = tuple((((x, 7.525), (x, y + 0.95)), None) for x in (2.3, 7.2, 12.1, 17.0))
    _draw(nodes, edges, "architecture.png", polylines)


def search_cache_flow() -> None:
    w = 9.4
    nodes = [
        Node("start", 4.7, 21.2, "Начало: параметры запроса", "start", w),
        Node("gen", 4.7, 19.8, "Прочитать номер поколения из кэша", width=w),
        Node("key", 4.7, 18.4, "Ключ = поколение + SHA-256 от параметров", width=w),
        Node("get", 4.7, 17.0, "Прочитать запись по ключу", width=w),
        Node("hit", 4.7, 15.1, "Запись\nнайдена?", "cond", 4.2, 1.9),
        Node("ret1", 12.6, 15.1, "Вернуть запись,\nX-Cache: HIT", "start", 4.6, 1.1),
        Node("pgfts", 4.7, 12.8, "Метод\npgfts?", "cond", 4.2, 1.9),
        Node("scores", 12.6, 12.8, "Получить оценки\nот PostgreSQL", width=4.6, height=1.1),
        Node("own", 4.7, 10.6, "Посчитать оценки по индексу в памяти", width=w),
        Node("rank", 4.7, 9.2, "Отобрать, отсортировать и выбрать страницу", width=w),
        Node("snip", 4.7, 7.8, "Построить фрагменты с подсветкой", width=w),
        Node("put", 4.7, 6.4, "Записать выдачу в кэш с TTL", width=w),
        Node("end", 4.7, 5.0, "Вернуть выдачу, X-Cache: MISS", "start", w),
    ]
    edges = [
        ("start", "gen"),
        ("gen", "key"),
        ("key", "get"),
        ("get", "hit"),
        ("hit", "ret1", "да"),
        ("hit", "pgfts", "нет"),
        ("pgfts", "scores", "да"),
        ("pgfts", "own", "нет"),
        ("own", "rank"),
        ("rank", "snip"),
        ("snip", "put"),
        ("put", "end"),
    ]
    polylines = ((((12.6, 12.25), (12.6, 9.2), (9.4, 9.2)), None),)
    _draw(nodes, edges, "search_cache_flow.png", polylines)


def save_flow() -> None:
    w = 9.4
    nodes = [
        Node("start", 4.7, 26.4, "Начало: идентификатор, текст, ожидаемая версия", "start", w),
        Node("read", 4.7, 25.0, "Прочитать документ", width=w),
        Node("found", 4.7, 23.1, "Документ\nесть?", "cond", 4.2, 1.9),
        Node("e404a", 12.8, 23.1, "Ошибка 404", "start", 3.6),
        Node("parse", 4.7, 21.2, "Проверить и разобрать новый текст", width=w),
        Node("valid", 4.7, 19.3, "Текст\nверен?", "cond", 4.2, 1.9),
        Node("e422", 12.8, 19.3, "Ошибка 422", "start", 3.6),
        Node("lock", 4.7, 17.4, "Открыть транзакцию, заблокировать строку", width=w),
        Node("exists", 4.7, 15.5, "Строка\nна месте?", "cond", 4.2, 1.9),
        Node("e404", 12.8, 15.5, "Ошибка 404", "start", 3.6),
        Node("ver", 4.7, 13.2, "Версия\nсовпала?", "cond", 4.2, 1.9),
        Node("e409", 12.8, 13.2, "Ошибка 409", "start", 3.6),
        Node("hash", 4.7, 10.9, "Текст\nизменился?", "cond", 4.2, 1.9),
        Node("same", 12.8, 10.9, "Вернуть текущую\nверсию", "start", 3.6, 1.1),
        Node("write", 4.7, 8.9, "Номер версии + 1, записать версию и поля", width=w),
        Node("commit", 4.7, 7.5, "Зафиксировать транзакцию", width=w),
        Node("index", 4.7, 6.1, "Заменить документ в индексе", width=w),
        Node("reset", 4.7, 4.7, "Увеличить номер поколения кэша", width=w),
        Node("end", 4.7, 3.3, "Вернуть новую версию", "start", w),
    ]
    edges = [
        ("start", "read"),
        ("read", "found"),
        ("found", "parse", "да"),
        ("found", "e404a", "нет"),
        ("parse", "valid"),
        ("valid", "lock", "да"),
        ("valid", "e422", "нет"),
        ("lock", "exists"),
        ("exists", "ver", "да"),
        ("exists", "e404", "нет"),
        ("ver", "hash", "да"),
        ("ver", "e409", "нет"),
        ("hash", "write", "да"),
        ("hash", "same", "нет"),
        ("write", "commit"),
        ("commit", "index"),
        ("index", "reset"),
        ("reset", "end"),
    ]
    _draw(nodes, edges, "save_flow.png")


def _sequence(participants: list[str], messages: list[tuple], name: str) -> None:
    """Диаграмма последовательности. Сообщение: (откуда, куда, текст[, 'return'])."""
    step_x, step_y, box_width = 4.5, 1.0, 4.1
    positions = {title: index * step_x for index, title in enumerate(participants)}
    height = (len(messages) + 1) * step_y + 1.5
    left = -box_width / 2 - 0.15
    width = (len(participants) - 1) * step_x + box_width + 0.3
    fig = plt.figure(figsize=(width * INCHES_PER_UNIT, height * INCHES_PER_UNIT))
    ax = fig.add_axes((0, 0, 1, 1))
    top = height - 1.25
    for title, x in positions.items():
        ax.add_patch(
            Rectangle(
                (x - box_width / 2, top),
                box_width,
                1.1,
                facecolor=FILL["proc"],
                edgecolor=EDGE,
                linewidth=1.2,
            )
        )
        ax.text(x, top + 0.55, title, ha="center", va="center", fontsize=14)
        ax.plot([x, x], [0.2, top], color="#9AA0A6", linewidth=1, linestyle=(0, (4, 3)))
    y = top - 0.7
    for message in messages:
        source, target, text, *kind = message
        x1, x2 = positions[source], positions[target]
        ax.add_patch(
            FancyArrowPatch(
                (x1, y),
                (x2, y),
                arrowstyle="-|>",
                mutation_scale=13,
                color=EDGE,
                linewidth=1.2,
                linestyle=(0, (4, 3)) if kind else "-",
                shrinkA=0,
                shrinkB=0,
            )
        )
        ax.text(
            (x1 + x2) / 2,
            y + 0.09,
            text,
            ha="center",
            va="bottom",
            fontsize=13,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1},
        )
        y -= step_y
    ax.set_xlim(left, left + width)
    ax.set_ylim(0, height)
    ax.axis("off")
    fig.savefig(OUT / name, dpi=200, facecolor="white")
    plt.close(fig)


def sequences() -> None:
    api, service = "REST API", "KnowledgeBase"
    cache, core, db = "Кэш (Redis)", "Индекс в памяти", "PostgreSQL"
    _sequence(
        [api, service, cache, core, db],
        [
            (api, service, "search(query)"),
            (service, cache, "GET поколение, ключ"),
            (cache, service, "запись найдена", "return"),
            (service, api, "выдача, X-Cache: HIT", "return"),
            (api, service, "search(другой запрос)"),
            (service, cache, "GET поколение, ключ"),
            (cache, service, "пусто", "return"),
            (service, db, "fulltext_scores (только метод pgfts)"),
            (db, service, "оценки документов", "return"),
            (service, core, "search или rank_scores"),
            (core, service, "результаты и фрагменты", "return"),
            (service, cache, "SET ключ, TTL"),
            (service, api, "выдача, X-Cache: MISS", "return"),
        ],
        "sequence_search.png",
    )
    _sequence(
        [api, service, db, core, cache],
        [
            (api, service, "update_document"),
            (service, db, "SELECT документ"),
            (db, service, "строка", "return"),
            (service, db, "SELECT … FOR UPDATE"),
            (db, service, "строка заблокирована", "return"),
            (service, db, "INSERT, UPDATE, COMMIT"),
            (service, core, "add_document (замена)"),
            (service, cache, "INCR поколение"),
            (service, api, "новая версия", "return"),
        ],
        "sequence_update.png",
    )


@dataclass(frozen=True)
class Members:
    fields: list[str]
    methods: list[str]


def class_members(path: Path, name: str) -> Members:
    """Поля и методы класса из исходного кода. Унаследованные методы не повторяются."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    fields: list[str] = []
    methods: list[str] = []
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            fields.append(item.target.id)
        elif isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
            fields.append(item.targets[0].id)
        elif isinstance(item, ast.FunctionDef):
            if item.name == "__init__":
                for inner in ast.walk(item):
                    target = None
                    if isinstance(inner, ast.Assign):
                        target = inner.targets[0]
                    elif isinstance(inner, ast.AnnAssign):
                        target = inner.target
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and target.attr not in fields
                    ):
                        fields.append(target.attr)
            elif not item.name.startswith("__"):
                methods.append(item.name)
    return Members(fields, methods)


def _sign(member: str) -> str:
    return "−" if member.startswith("_") else "+"


@dataclass
class Box:
    title: str
    x: float
    top: float
    width: float
    fields: list[str]
    methods: list[str]
    abstract: bool = False

    @property
    def height(self) -> float:
        rows = 1 + (len(self.fields) or 0) + len(self.methods)
        return rows * LINE + 0.35 + (0.2 if self.abstract else 0)

    @property
    def bottom(self) -> float:
        return self.top - self.height

    @property
    def center(self) -> float:
        return self.x + self.width / 2


def _draw_box(ax: plt.Axes, box: Box) -> None:
    ax.add_patch(
        Rectangle(
            (box.x, box.bottom),
            box.width,
            box.height,
            facecolor="white",
            edgecolor=EDGE,
            linewidth=1.3,
        )
    )
    header = LINE + 0.2 + (0.2 if box.abstract else 0)
    ax.add_patch(
        Rectangle(
            (box.x, box.top - header),
            box.width,
            header,
            facecolor=FILL["proc"],
            edgecolor=EDGE,
            linewidth=1.3,
        )
    )
    title = f"«абстрактный»\n{box.title}" if box.abstract else box.title
    ax.text(
        box.center,
        box.top - header / 2,
        title,
        ha="center",
        va="center",
        fontsize=MEMBER_FONT - (2 if box.abstract else 0),
        fontweight="bold",
    )
    y = box.top - header - 0.35
    for member in box.fields:
        ax.text(box.x + 0.15, y, f"{_sign(member)} {member}", fontsize=MEMBER_FONT, va="center")
        y -= LINE
    if box.fields and box.methods:
        ax.plot([box.x, box.x + box.width], [y + LINE / 2, y + LINE / 2], color=EDGE, linewidth=0.8)
    for member in box.methods:
        ax.text(box.x + 0.15, y, f"{_sign(member)} {member}()", fontsize=MEMBER_FONT, va="center")
        y -= LINE


def _inherits(ax: plt.Axes, child: Box, parent: Box) -> None:
    ax.add_patch(
        FancyArrowPatch(
            (child.center, child.top),
            (parent.center, parent.bottom),
            arrowstyle="-|>",
            mutation_scale=20,
            facecolor="white",
            edgecolor=EDGE,
            linewidth=1.3,
            shrinkA=0,
            shrinkB=0,
        )
    )


def _uses(ax: plt.Axes, start: tuple[float, float], end: tuple[float, float], label: str) -> None:
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="->",
            mutation_scale=16,
            color=EDGE,
            linewidth=1.3,
            shrinkA=0,
            shrinkB=0,
        )
    )
    ax.text(
        (start[0] + end[0]) / 2,
        (start[1] + end[1]) / 2 + 0.1,
        label,
        ha="center",
        va="bottom",
        fontsize=12,
    )


def classes() -> None:
    service = class_members(SRC / "service.py", "KnowledgeBase")
    repo_path = SRC / "storage" / "repository.py"
    repo = class_members(repo_path, "DocumentRepository")
    sql = class_members(repo_path, "SqlDocumentRepository")
    cache_path = SRC / "cache.py"
    search_cache = class_members(cache_path, "SearchCache")
    cache = class_members(cache_path, "Cache")
    memory = class_members(cache_path, "MemoryCache")
    redis_cache = class_members(cache_path, "RedisCache")
    engine = class_members(SRC / "search.py", "SearchEngine")

    top = 19.0
    a = Box("KnowledgeBase", 0, top, 7.0, service.fields, service.methods)
    b = Box("DocumentRepository", 9.4, top, 7.0, repo.fields, repo.methods, abstract=True)
    private = [m for m in sql.methods if m.startswith("_")]
    c = Box("SqlDocumentRepository", 9.4, b.bottom - 1.0, 7.0, sql.fields, private)
    d = Box("SearchCache", 18.8, top, 8.4, search_cache.fields, search_cache.methods)
    e = Box("Cache", 18.8, d.bottom - 1.0, 8.4, [], cache.methods, abstract=True)
    f = Box("NullCache", 18.8, e.bottom - 1.0, 2.5, [], [])
    g = Box(
        "MemoryCache",
        21.5,
        e.bottom - 1.0,
        2.9,
        [m for m in memory.fields if m.startswith("_")],
        [],
    )
    h = Box(
        "RedisCache",
        24.6,
        e.bottom - 1.0,
        2.9,
        redis_cache.fields,
        ["from_url"],
    )
    i = Box("SearchEngine", 29.4, top, 6.4, engine.fields, engine.methods)
    boxes = [a, b, c, d, e, f, g, h, i]

    bottom = min(box.bottom for box in boxes) - 0.3
    right = max(box.x + box.width for box in boxes) + 0.3
    fig = plt.figure(
        figsize=((right + 0.3) * INCHES_PER_UNIT, (top + 0.3 - bottom) * INCHES_PER_UNIT)
    )
    ax = fig.add_axes((0, 0, 1, 1))
    for box in boxes:
        _draw_box(ax, box)
    _inherits(ax, c, b)
    _inherits(ax, f, e)
    _inherits(ax, g, e)
    _inherits(ax, h, e)
    _uses(ax, (a.x + a.width, top - 1.2), (b.x, top - 1.2), "_repository")
    _uses(ax, (d.center, d.bottom), (e.center, e.top), "")
    ax.text(d.center + 0.15, (d.bottom + e.top) / 2, "_cache", va="center", fontsize=12)
    # Связи сервиса с кэшем и движком идут над блоками, чтобы не пересекать их.
    level = top + 0.75
    ax.plot([a.center, a.center, d.center], [top, level, level], color=EDGE, linewidth=1.3)
    _uses(ax, (d.center, level), (d.center, top), "")
    ax.text(
        (a.center + d.center) / 2, level + 0.08, "_cache", ha="center", va="bottom", fontsize=12
    )
    level2 = top + 1.5
    ax.plot(
        [a.center - 1.5, a.center - 1.5, i.center], [top, level2, level2], color=EDGE, linewidth=1.3
    )
    _uses(ax, (i.center, level2), (i.center, top), "")
    ax.text(
        (a.center + i.center) / 2, level2 + 0.08, "_engine", ha="center", va="bottom", fontsize=12
    )
    ax.set_xlim(-0.3, right)
    ax.set_ylim(bottom, top + 2.1)
    ax.axis("off")
    fig.set_size_inches((right + 0.3) * INCHES_PER_UNIT, (top + 2.1 - bottom) * INCHES_PER_UNIT)
    fig.savefig(OUT / "classes.png", dpi=170, facecolor="white")
    plt.close(fig)


def scorers() -> None:
    path = SRC / "scoring.py"
    scorer = class_members(path, "Scorer")
    top = 8.0
    parent = Box("Scorer", 6.0, top, 8.0, scorer.fields, scorer.methods, abstract=True)
    children = [
        Box(name, x, parent.bottom - 1.2, 6.2, *_fields_methods(path, name))
        for name, x in (("TfIdfScorer", 0), ("Bm25Scorer", 6.9), ("HybridScorer", 13.8))
    ]
    bottom = min(box.bottom for box in children) - 0.3
    right = children[-1].x + children[-1].width + 0.3
    fig = plt.figure(
        figsize=((right + 0.3) * INCHES_PER_UNIT, (top + 0.3 - bottom) * INCHES_PER_UNIT)
    )
    ax = fig.add_axes((0, 0, 1, 1))
    for box in [parent, *children]:
        _draw_box(ax, box)
    for child in children:
        _inherits(ax, child, parent)
    ax.set_xlim(-0.3, right)
    ax.set_ylim(bottom, top + 0.3)
    ax.axis("off")
    fig.savefig(OUT / "scorers.png", dpi=170, facecolor="white")
    plt.close(fig)


def _fields_methods(path: Path, name: str) -> tuple[list[str], list[str]]:
    members = class_members(path, name)
    return members.fields, members.methods


def er_diagram() -> None:
    tables = Base.metadata.tables
    boxes: dict[str, Box] = {}

    def rows(table_name: str) -> list[str]:
        result = []
        for column in tables[table_name].columns:
            marks = []
            if column.primary_key:
                marks.append("PK")
            if column.foreign_keys:
                marks.append("FK")
            if column.unique:
                marks.append("UNIQUE")
            if column.computed is not None:
                marks.append("GENERATED")
            elif column.nullable and not column.primary_key:
                marks.append("NULL")
            suffix = f" [{', '.join(marks)}]" if marks else ""
            sql_type = column.type.compile(dialect=postgresql.dialect()).lower()
            result.append(f"{column.name}: {sql_type}{suffix}")
        return result

    documents = rows("documents")
    versions = rows("document_versions")
    top = 10.0
    boxes["documents"] = Box("documents", 0, top, 9.5, documents, [])
    boxes["versions"] = Box("document_versions", 12.0, top, 9.5, versions, [])
    bottom = min(box.bottom for box in boxes.values()) - 0.3
    right = 21.8
    fig = plt.figure(
        figsize=((right + 0.3) * INCHES_PER_UNIT, (top + 0.3 - bottom) * INCHES_PER_UNIT)
    )
    ax = fig.add_axes((0, 0, 1, 1))
    for box in boxes.values():
        ax.add_patch(
            Rectangle(
                (box.x, box.bottom),
                box.width,
                box.height,
                facecolor="white",
                edgecolor=EDGE,
                linewidth=1.3,
            )
        )
        header = LINE + 0.2
        ax.add_patch(
            Rectangle(
                (box.x, box.top - header),
                box.width,
                header,
                facecolor=FILL["proc"],
                edgecolor=EDGE,
                linewidth=1.3,
            )
        )
        ax.text(
            box.center,
            box.top - header / 2,
            box.title,
            ha="center",
            va="center",
            fontsize=14,
            fontweight="bold",
        )
        y = box.top - header - 0.35
        for row in box.fields:
            ax.text(box.x + 0.2, y, row, fontsize=13, va="center")
            y -= LINE
    ax.plot([9.5, 12.0], [top - 1.2, top - 1.2], color=EDGE, linewidth=1.3)
    ax.text(9.7, top - 1.1, "1", fontsize=13, va="bottom")
    ax.text(11.8, top - 1.1, "N", fontsize=13, va="bottom", ha="right")
    ax.set_xlim(-0.3, right)
    ax.set_ylim(bottom, top + 0.3)
    ax.axis("off")
    fig.savefig(OUT / "er.png", dpi=170, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    architecture()
    search_cache_flow()
    save_flow()
    sequences()
    classes()
    scorers()
    er_diagram()
    print("Сохранено:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))
