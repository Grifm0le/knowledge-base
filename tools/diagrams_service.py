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
        Node("client", 9.8, 11.6, "Клиенты: веб-интерфейс, Swagger UI, curl", "ext", 19.4, 0.95),
        Node(
            "api",
            9.8,
            9.8,
            "REST API, пакет kb.api: методы, схемы, ошибки, журнал, проверка токена",
            width=19.4,
            height=0.95,
        ),
        Node(
            "service",
            9.8,
            8.0,
            "KnowledgeBase и Workspace: документы пользователя. AuthService: вход и токены",
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
        Node(
            "cache",
            12.1,
            y,
            "Кэш и лимиты\nSearchCache, RateLimiter,\nRedisCache",
            width=w,
            height=1.9,
        ),
        Node(
            "repo",
            17.0,
            y,
            "Хранилище\nDocumentRepository,\nUserRepository",
            width=w,
            height=1.9,
        ),
        Node(
            "memory",
            7.2,
            2.5,
            "Индекс каждого\nпользователя\nв памяти процесса",
            width=w,
            height=1.3,
        ),
        Node(
            "redis", 12.1, 2.5, "Redis\nзаписи с TTL, поколения,\nсчётчики попыток", "ext", w, 1.5
        ),
        Node(
            "pg", 17.0, 2.5, "PostgreSQL\nдокументы, версии,\nпользователи, доступ", "ext", w, 1.5
        ),
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
    repo_path = SRC / "storage" / "repository.py"
    cache_path = SRC / "cache.py"
    scoring_path = SRC / "scoring.py"
    service = class_members(SRC / "service.py", "KnowledgeBase")
    workspace = class_members(SRC / "service.py", "Workspace")
    indexes = class_members(SRC / "service.py", "SearchIndexes")
    repo = class_members(repo_path, "DocumentRepository")
    sql = class_members(repo_path, "SqlDocumentRepository")
    engine = class_members(SRC / "search.py", "SearchEngine")
    search_cache = class_members(cache_path, "SearchCache")
    cache = class_members(cache_path, "Cache")
    memory = class_members(cache_path, "MemoryCache")
    redis_cache = class_members(cache_path, "RedisCache")
    scorer = class_members(scoring_path, "Scorer")

    top, width, column2, column3 = 0.0, 6.6, 8.8, 17.6
    a = Box("KnowledgeBase", 0, top, width, service.fields, service.methods)
    b = Box("DocumentRepository", column2, top, width, repo.fields, repo.methods, abstract=True)
    private = [m for m in sql.methods if m.startswith("_")]
    c = Box("SqlDocumentRepository", column2, b.bottom - 1.0, width, sql.fields, private)
    w = Box("Workspace", 0, a.bottom - 1.0, width, workspace.fields, workspace.methods)
    i = Box("SearchIndexes", column3, top, width, indexes.fields, indexes.methods)
    j = Box("SearchEngine", column3, i.bottom - 1.0, width, engine.fields, engine.methods)
    d = Box(
        "SearchCache", column3, j.bottom - 1.0, width, search_cache.fields, search_cache.methods
    )
    e = Box("Cache", column3, d.bottom - 1.0, width, [], cache.methods, abstract=True)
    cache_children = e.bottom - 1.0
    f = Box("NullCache", 15.3, cache_children, 2.5, [], [])
    g = Box("MemoryCache", 18.1, cache_children, 2.9, memory.fields, [])
    h = Box("RedisCache", 21.3, cache_children, 2.9, redis_cache.fields, ["from_url"])

    s = Box(
        "Scorer",
        4.0,
        min(w.bottom, c.bottom) - 1.2,
        5.8,
        scorer.fields,
        scorer.methods,
        abstract=True,
    )
    scorers = [
        Box(name, x, s.bottom - 1.0, 4.4, members.fields, members.methods)
        for name, x, members in (
            ("TfIdfScorer", 0, class_members(scoring_path, "TfIdfScorer")),
            ("Bm25Scorer", 4.7, class_members(scoring_path, "Bm25Scorer")),
            ("HybridScorer", 9.4, class_members(scoring_path, "HybridScorer")),
        )
    ]
    boxes = [a, b, c, d, e, f, g, h, i, j, w, s, *scorers]

    level1, level2 = top + 0.75, top + 1.5
    bottom = min(box.bottom for box in boxes) - 0.3
    right = column3 + width + 0.3
    ceiling = level2 + 0.6
    figure_width, figure_height = right + 0.3, ceiling - bottom
    fig = plt.figure(figsize=(figure_width * INCHES_PER_UNIT, figure_height * INCHES_PER_UNIT))
    ax = fig.add_axes((0, 0, 1, 1))
    for box in boxes:
        _draw_box(ax, box)
    _inherits(ax, c, b)
    for child in (f, g, h):
        _inherits(ax, child, e)
    for child in scorers:
        _inherits(ax, child, s)
    _uses(ax, (a.x + a.width, top - 1.2), (b.x, top - 1.2), "_repository")
    _uses(ax, (a.center, a.bottom), (w.center, w.top), "")
    ax.text(a.center + 0.15, (a.bottom + w.top) / 2, "workspace()", va="center", fontsize=12)
    _uses(ax, (i.center, i.bottom), (j.center, j.top), "")
    ax.text(i.center + 0.15, (i.bottom + j.top) / 2, "_engines", va="center", fontsize=12)
    _uses(ax, (d.center, d.bottom), (e.center, e.top), "")
    ax.text(d.center + 0.15, (d.bottom + e.top) / 2, "_cache", va="center", fontsize=12)

    # Связи сервиса с кэшем и движком идут над блоками и в промежутке между столбцами.
    gap_x, cache_y = column3 - 1.1, d.top - 1.0
    ax.plot(
        [a.center, a.center, gap_x, gap_x],
        [top, level1, level1, cache_y],
        color=EDGE,
        linewidth=1.3,
    )
    _uses(ax, (gap_x, cache_y), (d.x, cache_y), "")
    ax.text((a.center + gap_x) / 2, level1 + 0.08, "_cache", ha="center", va="bottom", fontsize=12)
    start_x = a.center - 1.5
    ax.plot([start_x, start_x, i.center], [top, level2, level2], color=EDGE, linewidth=1.3)
    _uses(ax, (i.center, level2), (i.center, top), "")
    ax.text(
        (start_x + i.center) / 2, level2 + 0.08, "_indexes", ha="center", va="bottom", fontsize=12
    )

    ax.set_xlim(-0.3, right)
    ax.set_ylim(bottom, ceiling)
    ax.axis("off")
    fig.savefig(OUT / "classes.png", dpi=200, facecolor="white")
    plt.close(fig)


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

    top, width, gap = 10.0, 9.5, 1.2
    left, column2 = 0.0, 12.0
    boxes["users"] = Box("users", left, top, width, rows("users"), [])
    boxes["tokens"] = Box(
        "auth_tokens", left, boxes["users"].bottom - gap, width, rows("auth_tokens"), []
    )
    boxes["shares"] = Box(
        "document_shares", left, boxes["tokens"].bottom - gap, width, rows("document_shares"), []
    )
    boxes["documents"] = Box("documents", column2, top, width, rows("documents"), [])
    boxes["versions"] = Box(
        "document_versions",
        column2,
        boxes["documents"].bottom - gap,
        width,
        rows("document_versions"),
        [],
    )
    bottom = min(box.bottom for box in boxes.values()) - 0.3
    right = 21.8
    fig = plt.figure(
        figsize=((right + 1.2) * INCHES_PER_UNIT, (top + 0.3 - bottom) * INCHES_PER_UNIT)
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
    users, tokens, shares = boxes["users"], boxes["tokens"], boxes["shares"]
    documents, versions = boxes["documents"], boxes["versions"]

    def link(xs: list[float], ys: list[float], one: tuple, many: tuple) -> None:
        ax.plot(xs, ys, color=EDGE, linewidth=1.3)
        ax.text(*one, "1", fontsize=13, va="bottom")
        ax.text(*many, "N", fontsize=13, va="bottom", ha="right")

    link([9.5, 12.0], [top - 1.2, top - 1.2], (9.7, top - 1.1), (11.8, top - 1.1))
    link(
        [users.center, users.center],
        [users.bottom, tokens.top],
        (users.center + 0.15, users.bottom - 0.45),
        (users.center - 0.15, tokens.top + 0.05),
    )
    link(
        [documents.center, documents.center],
        [documents.bottom, versions.top],
        (documents.center + 0.15, documents.bottom - 0.45),
        (documents.center - 0.15, versions.top + 0.05),
    )
    # Строка доступа связывает пользователя и документ: линии идут в обход соседних таблиц.
    side_y = users.bottom + 0.5
    shares_y = shares.top - 1.2
    link(
        [left, -0.6, -0.6, left],
        [side_y, side_y, shares_y, shares_y],
        (-0.5, side_y + 0.05),
        (-0.1, shares_y + 0.05),
    )
    gap_x = 10.75
    doc_y = documents.bottom + 0.5
    link(
        [column2, gap_x, gap_x, 9.5],
        [doc_y, doc_y, shares_y, shares_y],
        (11.3, doc_y + 0.05),
        (10.2, shares_y + 0.05),
    )
    ax.set_xlim(-0.9, right + 0.3)
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
    er_diagram()
    print("Сохранено:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))
