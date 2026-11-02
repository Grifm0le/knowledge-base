"""Схемы развёртывания и защиты: python tools/diagrams_ops.py. Рисунки попадают в docs/img."""

from __future__ import annotations

from diagrams import FILL, Node, _draw

FILL.setdefault("ext", "#eef1f6")


def deployment() -> None:
    w = 4.6
    y = 3.6
    nodes = [
        Node("browser", 9.8, 13.0, "Браузер пользователя, клиенты API", "ext", 19.4, 0.95),
        Node(
            "proxy",
            9.8,
            10.9,
            "proxy: nginx. TLS, HSTS, журнал без строки запроса, ограничение частоты для входа.\n"
            "Опубликованные порты: 8080 (перевод на HTTPS) и 8443",
            width=19.4,
            height=1.4,
        ),
        Node(
            "app",
            9.8,
            8.3,
            "app: uvicorn и FastAPI, один рабочий процесс, порт 8000 только внутри сети.\n"
            "Пользователь без прав root, файловая система только для чтения, права ядра сброшены",
            width=19.4,
            height=1.4,
        ),
        Node(
            "migrate",
            2.3,
            y,
            "migrate:\nalembic upgrade head,\nработает один раз\nперед запуском",
            width=w,
            height=2.2,
        ),
        Node("db", 7.2, y, "db: PostgreSQL 18\nтом db_data\nна диске", "ext", w, 2.2),
        Node(
            "redis",
            12.1,
            y,
            "redis: Redis 8\nкэш и счётчики,\nна диск не пишет,\nпароль",
            "ext",
            w,
            2.2,
        ),
        Node(
            "mail",
            17.0,
            y,
            "mailpit: SMTP 1025\nпочта для проверки,\nв работе заменяется\nнастоящим SMTP",
            "ext",
            w,
            2.2,
        ),
    ]
    edges = [
        ("browser", "proxy", "HTTPS"),
        ("proxy", "app", "HTTP, сеть frontend"),
        ("migrate", "db"),
    ]
    polylines = (
        (((7.2, 7.6), (7.2, 6.2), (7.2, y + 1.1)), None),
        (((12.1, 7.6), (12.1, 6.2), (12.1, y + 1.1)), None),
        (((17.0, 7.6), (17.0, 6.2), (17.0, y + 1.1)), None),
        (((7.2, 6.0), (7.2, 5.9)), "SQL, сеть backend"),
        (((12.1, 6.0), (12.1, 5.9)), "RESP, сеть backend"),
        (((17.0, 6.0), (17.0, 5.9)), "SMTP, сеть backend"),
    )
    _draw(nodes, edges, "deployment.png", polylines)


def login_flow() -> None:
    w = 9.4
    nodes = [
        Node("start", 4.7, 16.6, "POST /auth/login: адрес и пароль", "start", w),
        Node("limit", 4.7, 14.7, "Лимит попыток\nисчерпан?", "cond", 4.6, 1.9),
        Node("too", 12.6, 14.7, "Ответ 429\nи заголовок Retry-After", "start", 5.0, 1.1),
        Node("find", 4.7, 12.8, "Найти учётную запись по адресу", width=w),
        Node("found", 4.7, 10.9, "Запись\nнайдена?", "cond", 4.6, 1.9),
        Node(
            "dummy", 12.9, 10.9, "Проверить пароль по заглушке:\nвремя то же", width=5.0, height=1.1
        ),
        Node("right", 4.7, 8.6, "Пароль\nверен?", "cond", 4.6, 1.9),
        Node("wrong", 12.6, 8.6, "Увеличить счётчики,\nответ 401", "start", 5.0, 1.1),
        Node("ok", 4.7, 6.3, "Адрес\nподтверждён?", "cond", 4.6, 1.9),
        Node("no", 12.6, 6.3, "Ответ 403\nemail_not_verified", "start", 5.0, 1.1),
        Node("reset", 4.7, 4.4, "Сбросить счётчик попыток", width=w),
        Node("issue", 4.7, 3.0, "Выдать токен доступа и cookie с токеном обновления", width=w),
        Node("end", 4.7, 1.6, "Ответ 200", "start", w),
    ]
    edges = [
        ("start", "limit"),
        ("limit", "too", "да"),
        ("limit", "find", "нет"),
        ("find", "found"),
        ("found", "dummy", "нет"),
        ("found", "right", "да"),
        ("dummy", "wrong"),
        ("right", "wrong", "нет"),
        ("right", "ok", "да"),
        ("ok", "no", "нет"),
        ("ok", "reset", "да"),
        ("reset", "issue"),
        ("issue", "end"),
    ]
    _draw(nodes, edges, "login_flow.png")


def recovery_flow() -> None:
    w = 9.4
    nodes = [
        Node("start", 4.7, 20.6, "POST /auth/password/forgot: адрес", "start", w),
        Node("limit", 4.7, 18.7, "Лимит запросов\nисчерпан?", "cond", 4.6, 1.9),
        Node("too", 12.6, 18.7, "Ответ 429", "start", 5.0, 1.1),
        Node("known", 4.7, 16.4, "Адрес\nесть в базе?", "cond", 4.6, 1.9),
        Node(
            "silent", 12.6, 16.4, "Ответ 202, такой же,\nписьмо не отправляется", "start", 5.0, 1.1
        ),
        Node("token", 4.7, 14.5, "Создать токен, сохранить его SHA-256 и срок", width=w),
        Node("send", 4.7, 13.1, "Ответ 202, после него письмо со ссылкой", width=w),
        Node("user", 4.7, 11.7, "Пользователь открывает ссылку, вводит новый пароль", "ext", w),
        Node("reset", 4.7, 10.3, "POST /auth/password/reset: токен и пароль", "start", w),
        Node("valid", 4.7, 8.4, "Токен\nдействует?", "cond", 4.6, 1.9),
        Node("bad", 12.6, 8.4, "Ответ 400", "start", 5.0, 1.1),
        Node("rules", 4.7, 6.1, "Пароль\nпо правилам?", "cond", 4.6, 1.9),
        Node("weak", 12.6, 6.1, "Ответ 422,\nтокен не потрачен", "start", 5.0, 1.1),
        Node("spend", 4.7, 4.2, "Погасить токен одним запросом UPDATE", width=w),
        Node("save", 4.7, 2.8, "Сохранить хэш, увеличить версию токенов", width=w),
        Node("end", 4.7, 1.4, "Ответ 204, все прежние сеансы завершены", "start", w),
    ]
    edges = [
        ("start", "limit"),
        ("limit", "too", "да"),
        ("limit", "known", "нет"),
        ("known", "silent", "нет"),
        ("known", "token", "да"),
        ("token", "send"),
        ("send", "user"),
        ("user", "reset"),
        ("reset", "valid"),
        ("valid", "bad", "нет"),
        ("valid", "rules", "да"),
        ("rules", "weak", "нет"),
        ("rules", "spend", "да"),
        ("spend", "save"),
        ("save", "end"),
    ]
    _draw(nodes, edges, "recovery_flow.png")


def access_flow() -> None:
    w = 9.4
    nodes = [
        Node("start", 4.7, 13.4, "Запрос к документу с токеном доступа", "start", w),
        Node("sign", 4.7, 11.5, "Подпись, срок,\nназначение верны?", "cond", 5.2, 1.9),
        Node("bad1", 12.6, 11.5, "Ответ 401", "start", 5.0, 1.1),
        Node("version", 4.7, 9.2, "Версия токена\nкак в базе?", "cond", 5.2, 1.9),
        Node("bad2", 12.6, 9.2, "Ответ 401: токен отозван", "start", 5.0, 1.1),
        Node("level", 4.7, 7.3, "Определить уровень: владелец, правка, чтение", width=w),
        Node("any", 4.7, 5.4, "Доступ\nесть?", "cond", 5.2, 1.9),
        Node("missing", 12.6, 5.4, "Ответ 404, как у\nнесуществующего", "start", 5.0, 1.1),
        Node("enough", 4.7, 3.1, "Уровня\nдостаточно?", "cond", 5.2, 1.9),
        Node("denied", 12.6, 3.1, "Ответ 403", "start", 5.0, 1.1),
        Node("end", 4.7, 1.2, "Выполнить действие от имени пользователя", "start", w),
    ]
    edges = [
        ("start", "sign"),
        ("sign", "bad1", "нет"),
        ("sign", "version", "да"),
        ("version", "bad2", "нет"),
        ("version", "level", "да"),
        ("level", "any"),
        ("any", "missing", "нет"),
        ("any", "enough", "да"),
        ("enough", "denied", "нет"),
        ("enough", "end", "да"),
    ]
    _draw(nodes, edges, "access_flow.png")


def main() -> None:
    deployment()
    login_flow()
    recovery_flow()
    access_flow()


if __name__ == "__main__":
    main()
