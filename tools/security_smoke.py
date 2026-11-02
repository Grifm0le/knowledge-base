"""Проверка защиты на развёрнутом сервисе: python tools/security_smoke.py [адрес] [адрес Mailpit].

Каждая строка протокола: что отправлено, какой код получен и какой ожидался.
Код возврата 0, только если все проверки прошли.
"""

from __future__ import annotations

import base64
import json
import sys
import time
import uuid

import httpx
from stack_client import API, Mailbox, make_client, new_user

PASSWORD = "надёжная-фраза-для-проверки"
NEW_PASSWORD = "другая-надёжная-фраза-2026"


class Report:
    def __init__(self) -> None:
        self.failed = 0
        self.total = 0

    def check(self, name: str, actual: object, expected: object, note: str = "") -> None:
        self.total += 1
        passed = actual == expected
        self.failed += not passed
        mark = "успешно" if passed else "ОШИБКА"
        print(f"{name:<58} {actual!s:>5} ждали {expected!s:<5} {mark} {note}")


def forge(token: str, **changes: object) -> str:
    """Токен с изменёнными полями и прежней подписью."""
    head, body, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims.update(changes)
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{head}.{encoded}.{signature}"


def without_signature(token: str) -> str:
    """Токен с алгоритмом none и пустой подписью: известный приём обхода проверки."""
    _, body, _ = token.split(".")
    head = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    return f"{head}.{body}."


def main(base_url: str, mailpit_url: str) -> int:
    report = Report()
    mailbox = Mailbox(mailpit_url)
    suffix = uuid.uuid4().hex[:8]
    first_email = f"first-{suffix}@example.org"
    second_email = f"second-{suffix}@example.org"

    print("Транспорт и заголовки")
    plain = base_url.replace("https://", "http://").replace(":8443", ":8080")
    with httpx.Client(timeout=10) as client:
        response = client.get(f"{plain}/")
        report.check("HTTP переводится на HTTPS", response.status_code, 308)
    with make_client(base_url) as client:
        headers = client.get("/").headers
        report.check("HSTS", "max-age" in headers.get("strict-transport-security", ""), True)
        report.check(
            "CSP запрещает чужие скрипты",
            "default-src 'self'" in headers["content-security-policy"],
            True,
        )
        report.check("Запрет встраивания в чужие страницы", headers["x-frame-options"], "DENY")
        report.check(
            "Версия сервера скрыта", any(c.isdigit() for c in headers.get("server", "")), False
        )

    print("\nПорты внутренних служб не опубликованы")
    host = "127.0.0.1"
    for name, port in (("PostgreSQL", 5433), ("Redis", 6379)):
        try:
            httpx.get(f"http://{host}:{port}/", timeout=1)
            reachable = True
        except httpx.TransportError:
            reachable = False
        report.check(f"{name} на порту {port} недоступен снаружи", reachable, False)

    print("\nТокены")
    first = new_user(base_url, mailbox, first_email, PASSWORD)
    second = new_user(base_url, mailbox, second_email, PASSWORD)
    with make_client(base_url) as anonymous:
        report.check("Запрос без токена", anonymous.get(f"{API}/documents").status_code, 401)
        for name, token in (
            ("Изменён идентификатор пользователя", forge(first, sub=str(uuid.uuid4()))),
            ("Срок действия продлён без подписи", forge(first, exp=int(time.time()) + 10**8)),
            ("Алгоритм none без подписи", without_signature(first)),
            ("Просроченный токен", forge(first, exp=int(time.time()) - 10)),
            ("Произвольная строка", "abc.def.ghi"),
        ):
            response = anonymous.get(
                f"{API}/documents", headers={"Authorization": f"Bearer {token}"}
            )
            report.check(name, response.status_code, 401)

    print("\nИзоляция данных")
    with make_client(base_url, first) as owner, make_client(base_url, second) as stranger:
        created = owner.post(
            f"{API}/documents",
            json={"content": f"# Секрет {suffix}\n\nЗаметка о сейфе {suffix}.\n"},
        ).json()
        doc = f"{API}/documents/{created['id']}"
        report.check("Владелец читает документ", owner.get(doc).status_code, 200)
        report.check("Чужой читает документ", stranger.get(doc).status_code, 404)
        report.check(
            "Чужой меняет документ", stranger.put(doc, json={"content": "# x"}).status_code, 404
        )
        report.check("Чужой удаляет документ", stranger.delete(doc).status_code, 404)
        found = stranger.get(f"{API}/search", params={"q": suffix}).json()["total"]
        report.check("Чужой ищет текст документа", found, 0)
        pgfts = stranger.get(f"{API}/search", params={"q": suffix, "scorer": "pgfts"}).json()[
            "total"
        ]
        report.check("Чужой ищет средствами базы", pgfts, 0)

        print("\nДоступ по приглашению")
        shared = owner.put(f"{doc}/shares", json={"email": second_email, "permission": "read"})
        report.check("Владелец открывает на чтение", shared.status_code, 200)
        report.check("Приглашённый читает", stranger.get(doc).status_code, 200)
        report.check(
            "Приглашённый правит", stranger.put(doc, json={"content": "# x"}).status_code, 403
        )
        report.check("Приглашённый удаляет", stranger.delete(doc).status_code, 403)
        revoked = owner.delete(f"{doc}/shares/{shared.json()['user_id']}")
        report.check("Владелец отзывает доступ", revoked.status_code, 204)
        report.check("После отзыва документ закрыт", stranger.get(doc).status_code, 404)

        print("\nВвод данных")
        attack = "'; DROP TABLE documents; --"
        for scorer in ("bm25", "pgfts"):
            response = owner.get(f"{API}/search", params={"q": attack, "scorer": scorer})
            report.check(f"Строка SQL в поиске, метод {scorer}", response.status_code, 200)
        report.check("Таблица документов на месте", owner.get(doc).status_code, 200)
        report.check(
            "Вес вне диапазона",
            owner.get(f"{API}/search", params={"q": "a", "w_title": 1000}).status_code,
            422,
        )
        report.check(
            "Идентификатор не UUID", owner.get(f"{API}/documents/1%20OR%201=1").status_code, 422
        )
        report.check(
            "Пустой документ",
            owner.post(f"{API}/documents", json={"content": "   "}).status_code,
            422,
        )
        too_large = "x" * (5 * 1024 * 1024 + 1)
        report.check(
            "Документ больше 5 МБ",
            owner.post(f"{API}/documents", json={"content": too_large}).status_code,
            422,
        )
        html = owner.post(
            f"{API}/documents",
            json={"content": "# Т\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n"},
        ).json()
        preview = owner.get(f"{API}/documents/{html['id']}/preview").json()["html"]
        report.check("Скрипт в тексте экранирован", "<script>" in preview, False)
        report.check("Ссылка javascript отброшена", 'href="javascript' in preview, False)

    print("\nРегистрация")
    with make_client(base_url) as client:
        register = {"email": first_email, "password": PASSWORD, "consent": True}
        free = {"email": f"free-{suffix}@example.org", "password": PASSWORD, "consent": True}
        taken = client.post(f"{API}/auth/register", json=register)
        fresh = client.post(f"{API}/auth/register", json=free)
        report.check("Занятый адрес: код как у свободного", taken.status_code, fresh.status_code)
        report.check("Занятый адрес: текст как у свободного", taken.text == fresh.text, True)
        weak = {"email": f"weak-{suffix}@example.org", "password": "короткий", "consent": True}
        report.check(
            "Слабый пароль", client.post(f"{API}/auth/register", json=weak).status_code, 422
        )
        refused = {"email": f"noc-{suffix}@example.org", "password": PASSWORD, "consent": False}
        report.check(
            "Нет согласия на обработку",
            client.post(f"{API}/auth/register", json=refused).status_code,
            422,
        )

        print("\nПодбор пароля")
        guessed = {"email": second_email, "password": "неверный-пароль-1"}
        unknown = {"email": f"nobody-{suffix}@example.org", "password": "неверный-пароль-1"}
        wrong = client.post(f"{API}/auth/login", json=guessed)
        missing = client.post(f"{API}/auth/login", json=unknown)
        report.check("Неверный пароль", wrong.status_code, 401)
        report.check(
            "Ответы для неверного пароля и нет адреса совпадают", wrong.text == missing.text, True
        )
        codes = [client.post(f"{API}/auth/login", json=guessed).status_code for _ in range(4)]
        report.check("Ещё четыре неудачные попытки", codes, [401] * 4)
        blocked = client.post(
            f"{API}/auth/login", json={"email": second_email, "password": PASSWORD}
        )
        report.check("Шестая попытка даже с верным паролем", blocked.status_code, 429)
        report.check("Ответ говорит, когда повторить", blocked.headers.get("retry-after"), "900")

        print("\nВосстановление пароля")
        forgot = client.post(f"{API}/auth/password/forgot", json={"email": first_email})
        none = client.post(
            f"{API}/auth/password/forgot", json={"email": f"nobody-{suffix}@example.org"}
        )
        report.check("Известный адрес", forgot.status_code, 202)
        report.check("Ответ не выдаёт, есть ли адрес", forgot.text == none.text, True)
        previous = mailbox.last_token(first_email)
        link = mailbox.last_token(first_email)
        reset = client.post(
            f"{API}/auth/password/reset", json={"token": "выдуманный", "new_password": NEW_PASSWORD}
        )
        report.check("Выдуманная ссылка", reset.status_code, 400)
        reset = client.post(
            f"{API}/auth/password/reset", json={"token": link, "new_password": NEW_PASSWORD}
        )
        report.check("Настоящая ссылка", reset.status_code, 204)
        again = client.post(
            f"{API}/auth/password/reset", json={"token": link, "new_password": PASSWORD}
        )
        report.check("Повторное использование ссылки", again.status_code, 400)
        response = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {first}"})
        report.check("Старый токен после смены пароля", response.status_code, 401)
        old = client.post(f"{API}/auth/login", json={"email": first_email, "password": PASSWORD})
        report.check("Вход со старым паролем", old.status_code, 401)
        new = client.post(
            f"{API}/auth/login", json={"email": first_email, "password": NEW_PASSWORD}
        )
        report.check("Вход с новым паролем", new.status_code, 200)
        cookie = new.headers.get("set-cookie", "")
        report.check("Cookie: HttpOnly", "HttpOnly" in cookie, True)
        report.check("Cookie: Secure", "Secure" in cookie, True)
        report.check("Cookie: SameSite", "samesite=strict" in cookie.lower(), True)
        del previous

    print(f"\nПроверок: {report.total}, ошибок: {report.failed}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "https://127.0.0.1:8443"
    mail = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8025"
    raise SystemExit(main(base, mail))
