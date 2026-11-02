"""Проверка развёрнутого сервиса по сценарию: python tools/api_smoke.py [адрес] [адрес Mailpit].

Регистрирует пользователя и подтверждает адрес по письму, импортирует заметки из samples, ищет,
меняет документ, смотрит версии и удаляет его.
Выводит код ответа и главное из каждого ответа, поэтому вывод можно читать как протокол.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import httpx
from stack_client import Mailbox, make_client, new_user

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
NOTE = """---
title: Проверочная заметка
tags: [проверка, api]
category: Служебное
---
# Проверочная заметка

Сервис сохраняет каждую правку как отдельную версию.
"""


def step(name: str, response: httpx.Response, summary: object = "") -> None:
    cache = response.headers.get("X-Cache", "-")
    elapsed = response.elapsed.total_seconds() * 1000
    print(f"{name:<34} {response.status_code} кэш={cache:<4} {elapsed:7.1f} мс  {summary}")


def main(base_url: str, mailpit_url: str) -> int:
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.org"
    token = new_user(base_url, Mailbox(mailpit_url), email, "надёжная-фраза-для-проверки")
    print(f"Пользователь зарегистрирован и вошёл по письму: {email}")
    with make_client(base_url + "/api/v1", token) as client:
        step("Состояние", (r := client.get("/health")), r.json())

        files = [
            ("files", (p.name, p.read_bytes(), "text/markdown"))
            for p in sorted(SAMPLES.glob("*.md"))
        ]
        r = client.post("/documents/import", files=files)
        body = r.json()
        counts = {name: len(body[name]) for name in ("created", "updated", "unchanged", "failed")}
        step("Импорт samples", r, counts)
        r = client.post("/documents/import", files=files)
        body = r.json()
        counts = {name: len(body[name]) for name in ("created", "updated", "unchanged", "failed")}
        step("Повторный импорт samples", r, counts)

        for name, params in (
            ("Поиск BM25, первый раз", {"q": "индексы для поиска"}),
            ("Поиск BM25, второй раз", {"q": "индексы для поиска"}),
            ("Поиск TF-IDF", {"q": "индексы для поиска", "scorer": "tfidf"}),
            ("Поиск гибридный", {"q": "индексы для поиска", "scorer": "hybrid"}),
            ("Поиск PostgreSQL FTS", {"q": "индексы для поиска", "scorer": "pgfts"}),
            ("Поиск с фильтром по тегу", {"q": "кэш", "tags": ["производительность"]}),
        ):
            r = client.get("/search", params=params)
            hits = r.json()["hits"]
            step(
                name, r, f"найдено {r.json()['total']}, первый: {hits[0]['title'] if hits else '-'}"
            )

        r = client.post("/documents", json={"content": NOTE})
        doc_id = r.json()["id"]
        step("Создание документа", r, f"версия {r.json()['version']}")
        r = client.get("/search", params={"q": "отдельную версию"})
        step("Поиск новой заметки", r, f"первый: {r.json()['hits'][0]['title']}")
        r = client.get("/search", params={"q": "отдельную версию"})
        step("Тот же поиск из кэша", r, f"первый: {r.json()['hits'][0]['title']}")

        changed = NOTE.replace("Сервис сохраняет", "Сервис надёжно сохраняет")
        r = client.put(f"/documents/{doc_id}", json={"content": changed, "expected_version": 1})
        step("Новая версия", r, f"версия {r.json()['version']}")
        r = client.put(
            f"/documents/{doc_id}", json={"content": changed + " ", "expected_version": 1}
        )
        step("Устаревшая версия", r, r.json()["error"]["code"])
        r = client.get("/search", params={"q": "отдельную версию"})
        step("Поиск после правки (кэш сброшен)", r, f"первый: {r.json()['hits'][0]['title']}")

        r = client.get(f"/documents/{doc_id}/versions")
        step("История версий", r, [item["version"] for item in r.json()])
        r = client.get(f"/documents/{doc_id}/diff", params={"base": 1, "target": 2})
        changed_lines = [
            ln
            for ln in r.json()["diff"].split("\n")
            if ln[:1] in "+-" and ln[:3] not in ("+++", "---")
        ]
        step("Различия версий", r, changed_lines)
        r = client.post(f"/documents/{doc_id}/versions/1/restore")
        step("Возврат версии 1", r, f"версия {r.json()['version']}")

        step(
            "Плохой документ",
            (r := client.post("/documents", json={"content": "   "})),
            r.json()["error"]["code"],
        )
        step(
            "Неизвестный метод",
            (r := client.get("/search", params={"q": "x", "scorer": "y"})),
            r.json()["error"]["code"],
        )
        r = client.get("/documents/00000000-0000-0000-0000-000000000000")
        step("Нет документа", r, r.json()["error"]["code"])
        step(
            "Неверный идентификатор", (r := client.get("/documents/abc")), r.json()["error"]["code"]
        )

        r = client.delete(f"/documents/{doc_id}")
        step("Удаление", r)
        r = client.get("/search", params={"q": "отдельную версию"})
        step("Поиск после удаления", r, f"найдено {r.json()['total']}")
        step("Статистика", (r := client.get("/stats")), r.json())
    return 0


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "https://127.0.0.1:8443"
    mail = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8025"
    raise SystemExit(main(base, mail))
