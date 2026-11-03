"""Перезапуск и сбои развёрнутого сервиса: python tools/ops_smoke.py [адрес] [адрес Mailpit].

Останавливает службы через docker compose и проверяет, что сервис ведёт себя как описано в
документации: данные остаются на месте, при сбое Redis поиск работает, при сбое базы клиент получает
503. Каждый шаг сравнивает результат с ожидаемым. Код возврата 1 означает, что есть ошибка.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import time
import uuid

from stack_client import API, Mailbox, make_client, new_user

PASSWORD = "надёжная-фраза-для-проверки"
TEXT = (
    "---\ntags: [проверка]\n---\n# Журнал обслуживания\n\nЗаметка переживает перезапуск службы.\n"
)


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failed = 0

    def section(self, title: str) -> None:
        print(f"\n{title}")

    def check(self, title: str, value: object, expected: object) -> None:
        self.total += 1
        passed = value == expected
        self.failed += not passed
        mark = "успешно" if passed else "ОШИБКА"
        print(f"{title:<52} {value!s:>10} ждали {expected!s:<10} {mark}")


def compose(command: list[str], *args: str) -> None:
    subprocess.run([*command, *args], check=True, capture_output=True)  # noqa: S603


def health(client) -> str:  # type: ignore[no-untyped-def]
    response = client.get(f"{API}/health")
    return f"{response.status_code} {response.json()['status']}"


def wait_health(client, expected: str, timeout: float = 90) -> None:  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        try:
            if health(client) == expected:
                return
        except Exception:  # noqa: BLE001, S110  # прокси или сервис ещё запускаются
            pass
        time.sleep(0.25)
    raise TimeoutError(f"Сервис не пришёл в состояние {expected}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", nargs="?", default="https://127.0.0.1:8443")
    parser.add_argument("mailpit", nargs="?", default="http://127.0.0.1:8025")
    parser.add_argument(
        "--compose", default="docker compose", help="команда запуска docker compose"
    )
    args = parser.parse_args()
    docker = shlex.split(args.compose)
    checks = Checks()

    email = f"ops-{uuid.uuid4().hex[:8]}@example.org"
    token = new_user(args.base, Mailbox(args.mailpit), email, PASSWORD)
    with make_client(args.base, token) as client:
        doc_id = client.post(f"{API}/documents", json={"content": TEXT}).json()["id"]

        def found() -> int:
            response = client.get(f"{API}/search", params={"q": "журнал обслуживания"})
            return int(response.json()["total"])

        checks.section("Перезапуск службы app")
        checks.check("До перезапуска документ находится", found(), 1)
        started = time.perf_counter()
        compose(docker, "restart", "app")
        wait_health(client, "200 ok")
        print(f"Сервис снова отвечает через {time.perf_counter() - started:.1f} с")
        checks.check(
            "Токен доступа действует после перезапуска",
            client.get(f"{API}/auth/me").status_code,
            200,
        )
        checks.check("Индекс восстановлен из базы, документ находится", found(), 1)

        checks.section("Остановка Redis")
        compose(docker, "stop", "redis")
        checks.check("Состояние сервиса", health(client), "200 degraded")
        checks.check("Поиск работает без кэша", found(), 1)
        response = client.put(
            f"{API}/documents/{doc_id}", json={"content": TEXT + "\nВторая версия.\n"}
        )
        checks.check("Сохранение работает", response.status_code, 200)
        compose(docker, "start", "redis")
        wait_health(client, "200 ok")
        checks.check("После запуска Redis сервис здоров", health(client), "200 ok")
        checks.check("Поиск работает с кэшем", found(), 1)

        checks.section("Остановка PostgreSQL")
        compose(docker, "stop", "db")
        checks.check("Состояние сервиса", client.get(f"{API}/health").status_code, 503)
        response = client.get(f"{API}/documents/{doc_id}")
        checks.check("Чтение документа, код ответа", response.status_code, 503)
        checks.check(
            "Чтение документа, код ошибки", response.json()["error"]["code"], "database_unavailable"
        )
        checks.check("В ответе нет трассировки", "Traceback" in response.text, False)
        compose(docker, "start", "db")
        wait_health(client, "200 ok")
        checks.check(
            "После запуска базы документ читается",
            client.get(f"{API}/documents/{doc_id}").status_code,
            200,
        )

        checks.section("Удаление и создание контейнеров: данные лежат в томе")
        compose(docker, "--profile", "demo", "down")
        compose(docker, "--profile", "demo", "up", "-d", "--wait")
        wait_health(client, "200 ok")
        checks.check("Документ на месте", client.get(f"{API}/documents/{doc_id}").status_code, 200)
        checks.check(
            "Текущая версия документа", client.get(f"{API}/documents/{doc_id}").json()["version"], 2
        )
        checks.check("Документ находится", found(), 1)

    print(f"\nПроверок: {checks.total}, ошибок: {checks.failed}")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
