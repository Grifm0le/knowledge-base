"""Проверка веб-интерфейса в настоящем браузере: python tools/ui_smoke.py [--shots docs/img].

Открывает Microsoft Edge без окна и проходит путь пользователя: регистрация, подтверждение, вход,
импорт, поиск, правка, версии, совместный доступ, статистика, смена и восстановление пароля.

С параметрами --base и --mailpit работает с развёрнутым сервисом и читает письма из Mailpit.
Без них запускает приложение в этом же процессе на настоящих PostgreSQL и Redis, а письма
перехватывает в памяти. База KB_UI_DATABASE_URL при этом очищается.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import threading
import time
import uuid
from pathlib import Path

import redis
import uvicorn
from alembic import command
from alembic.config import Config
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import create_engine, text
from stack_client import Mailbox

from kb.api.app import create_app
from kb.config import Settings
from kb.mailer import Mailer, OutgoingMail

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
PASSWORD = "надёжная-фраза-для-проверки"
NEW_PASSWORD = "другая-надёжная-фраза-2026"
TAG = uuid.uuid4().hex[:6]
FIRST = f"anna.{TAG}@example.org"
SECOND = f"boris.{TAG}@example.org"
XSS_NOTE = """---
title: Проверка экранирования
---
# Проверка экранирования

<img src=x onerror="window.xssFired = true">

<script>window.xssFired = true</script>

[ссылка](javascript:window.xssFired=true)
"""


class CapturingMailer(Mailer):
    """Складывает письма в список: сценарий открывает ссылки из них."""

    def __init__(self) -> None:
        self.sent: list[OutgoingMail] = []

    def send(self, mail: OutgoingMail) -> None:
        self.sent.append(mail)

    def last_link(self, to: str, expected: int = 1) -> str:
        for mail in reversed(self.sent):
            match = re.search(r"https?://\S+", mail.body)
            if mail.to == to and match:
                return match.group(0)
        raise AssertionError(f"Нет письма для {to}")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def prepare_database(url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    engine.dispose()


class Steps:
    """Записывает шаги и снимает экран: вывод читается как протокол проверки."""

    def __init__(self, page: Page, shots: Path) -> None:
        self.page = page
        self.shots = shots
        self.number = 0

    def done(self, name: str, shot: str | None = None) -> None:
        self.number += 1
        print(f"{self.number:>2}. {name}: успешно")
        if shot:
            self.page.screenshot(path=self.shots / f"ui_{shot}.png", full_page=True)


def register(
    page: Page, base: str, mailer: CapturingMailer | Mailbox, email: str, steps: Steps | None
) -> None:
    page.goto(f"{base}/#/register")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", "короткий")
    page.check("input[name=consent]")
    page.click("button[type=submit]")
    expect(page.locator("#message")).to_contain_text("короче")
    if steps:
        steps.done("Слабый пароль отклонён с понятной причиной", "register_error")
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    expect(page.locator("#message")).to_contain_text("отправлено письмо")
    if steps:
        steps.done("Регистрация принята, отправлено письмо", "register")
    page.goto(mailer.last_link(email))
    expect(page.locator("#message")).to_contain_text("Адрес подтверждён")
    if steps:
        steps.done("Адрес подтверждён по ссылке из письма")


def login(page: Page, base: str, email: str, password: str = PASSWORD) -> None:
    page.goto(f"{base}/#/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    expect(page.locator("#user-email")).to_have_text(email)
    expect(page.locator("h1")).to_have_text("Поиск")


def scenario(page: Page, base: str, mailer: CapturingMailer | Mailbox, shots: Path) -> int:
    steps = Steps(page, shots)
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(str(error)))
    dialogs: list[str] = []

    def accept(dialog) -> None:  # type: ignore[no-untyped-def]
        dialogs.append(dialog.message)
        dialog.accept()

    page.on("dialog", accept)

    page.goto(f"{base}/#/documents")
    expect(page.locator("h1")).to_have_text("Вход")
    steps.done("Без входа страница документов переводит на вход", "login")

    register(page, base, mailer, FIRST, steps)

    page.goto(f"{base}/#/login")
    page.fill("input[name=email]", FIRST)
    page.fill("input[name=password]", "неверный-пароль-1")
    page.click("button[type=submit]")
    expect(page.locator("#message")).to_contain_text("Неверный адрес или пароль")
    steps.done("Неверный пароль отклонён", "login_error")

    login(page, base, FIRST)
    steps.done("Вход выполнен")

    page.goto(f"{base}/#/documents")
    expect(page.locator("#drop")).to_be_visible()
    page.set_input_files("#file-input", sorted(str(path) for path in SAMPLES.glob("*.md")))
    expect(page.locator("#message")).to_contain_text("Создано")
    expect(page.locator("#documents tbody tr").first).to_be_visible()
    steps.done("Импортированы файлы примеров", "documents")

    page.goto(f"{base}/#/search")
    page.fill("input[name=q]", "индексы для поиска")
    page.click("button[type=submit]")
    expect(page.locator(".hit").first).to_be_visible()
    expect(page.locator(".hit mark").first).to_be_visible()
    steps.done("Поиск выдал результаты с подсветкой", "search")

    page.select_option("select[name=scorer]", "hybrid")
    page.click("#weights summary")
    page.locator("input[name=w_title]").fill("10")
    page.locator("input[name=w_body]").fill("0.5")
    page.click("button[type=submit]")
    expect(page.locator("#total")).to_contain_text("Найдено")
    steps.done("Гибридный метод со своими весами полей", "search_weights")

    page.goto(f"{base}/#/new")
    page.fill(
        "#editor",
        "---\ntitle: Заметка о кэше\ntags: [кэш]\n---\n# Заметка\n\nКэш ускоряет чтение.\n",
    )
    page.click("button[type=submit]")
    expect(page.locator("#doc-title")).to_have_text("Заметка о кэше")
    expect(page.locator(".preview h1")).to_have_text("Заметка")
    doc_url = page.url
    steps.done("Создан документ, показан просмотр", "document_view")

    page.click("button[data-tab=edit]")
    page.fill(
        "#editor",
        "---\ntitle: Заметка о кэше\ntags: [кэш]\n---\n"
        "# Заметка\n\nКэш снижает нагрузку на базу.\n",
    )
    steps.done("Открыт редактор", "document_edit")
    page.click("#tab-content button[type=submit]")
    expect(page.locator("#doc-version")).to_have_text("версия 2")
    steps.done("Сохранена новая версия")

    page.click("button[data-tab=versions]")
    page.click("#diff-run")
    expect(page.locator(".diff .add")).to_contain_text("снижает нагрузку")
    steps.done("Показаны версии и различия", "versions")

    page.click("button[data-restore='1']")
    expect(page.locator("#doc-version")).to_have_text("версия 3")
    steps.done("Старая версия возвращена как новая")

    page.goto(f"{base}/#/new")
    page.fill("#editor", XSS_NOTE)
    page.click("button[type=submit]")
    expect(page.locator(".preview h1")).to_have_text("Проверка экранирования")
    assert page.evaluate("window.xssFired") is None, "Сработал скрипт из документа"
    assert page.locator(".preview script").count() == 0
    assert page.locator(".preview img").count() == 0
    assert page.locator(".preview a[href^='javascript']").count() == 0
    steps.done("Разметка и скрипты из текста документа не выполняются", "xss")

    page.goto(f"{base}/#/stats")
    expect(page.locator("#stat-cards")).to_be_visible()
    steps.done("Статистика показана", "stats")

    page.goto(f"{base}/#/account")
    expect(page.locator("#personal-data")).to_contain_text(FIRST)
    steps.done("Показаны хранимые данные", "account")

    context = page.context.browser.new_context(
        viewport=page.viewport_size, locale="ru-RU", ignore_https_errors=True
    )
    second = context.new_page()
    second.on("pageerror", lambda error: problems.append(str(error)))
    register(second, base, mailer, SECOND, None)
    login(second, base, SECOND)
    second.goto(f"{base}/#/search")
    second.fill("input[name=q]", "кэш")
    second.click("button[type=submit]")
    expect(second.locator("#total")).to_have_text("Найдено: 0")
    second.goto(f"{base}/{doc_url[doc_url.index('#') :]}")
    expect(second.locator(".message.error")).to_contain_text("не найден")
    steps.done("Второй пользователь не видит чужой документ")
    second.screenshot(path=shots / "ui_foreign_document.png", full_page=True)

    page.goto(doc_url)
    page.click("button[data-tab=shares]")
    page.fill("input[name=share-email]", SECOND)
    page.click("#tab-content button[type=submit]")
    expect(page.locator("#message")).to_contain_text("Доступ открыт")
    steps.done("Владелец открыл документ на чтение", "shares")

    second.goto(f"{base}/#/search")
    second.fill("input[name=q]", "кэш")
    second.click("button[type=submit]")
    expect(second.locator("#total")).to_have_text("Найдено: 1")
    second.goto(f"{base}/{doc_url[doc_url.index('#') :]}")
    expect(second.locator("#doc-title")).to_have_text("Заметка о кэше")
    expect(second.locator("button[data-tab=edit]")).to_have_count(0)
    expect(second.locator("#tab-content")).to_contain_text("ускоряет")
    steps.done("Приглашённый видит документ, но не может править")
    second.screenshot(path=shots / "ui_shared_document.png", full_page=True)
    context.close()

    page.goto(f"{base}/#/account")
    page.fill("input[name=current-password]", PASSWORD)
    page.fill("input[name=new-password]", NEW_PASSWORD)
    page.locator("form", has_text="Смена пароля").locator("button").click()
    expect(page.locator("#message")).to_contain_text("Пароль изменён")
    steps.done("Пароль изменён")

    page.click("#logout")
    expect(page.locator("h1")).to_have_text("Вход")
    page.goto(f"{base}/#/forgot")
    page.fill("input[name=email]", FIRST)
    page.click("button[type=submit]")
    expect(page.locator("#message")).to_contain_text("отправлено письмо")
    steps.done("Запрошено восстановление пароля", "forgot")
    # На этот адрес уже приходило письмо с подтверждением.
    link = mailer.last_link(FIRST, expected=2)
    page.goto(link)
    page.fill("input[name=new-password]", PASSWORD)
    page.click("button[type=submit]")
    expect(page.locator("#message")).to_contain_text("Пароль изменён")
    steps.done("Пароль задан по ссылке из письма", "reset")
    login(page, base, FIRST)
    steps.done("Вход с восстановленным паролем")

    page.reload()
    expect(page.locator("#user-email")).to_have_text(FIRST)
    assert (
        page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
    )
    cookies_visible = page.evaluate("document.cookie")
    assert "kb_refresh" not in cookies_visible
    steps.done("После перезагрузки сеанс восстановлен, токен скрипту недоступен")

    page.goto(f"{base}/#/account")
    page.fill("input[name=delete-password]", PASSWORD)
    page.locator("form", has_text="Удаление учётной записи").locator("button").click()
    expect(page.locator("h1")).to_have_text("Вход")
    assert any("Удалить учётную запись" in message for message in dialogs)
    steps.done("Учётная запись удалена")

    if problems:
        print("Ошибки скриптов на странице:", problems)
        return 1
    print(f"Шагов: {steps.number}, ошибок скриптов на странице: 0")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shots", type=Path, default=ROOT / "docs" / "img")
    parser.add_argument("--base", help="адрес развёрнутого сервиса")
    parser.add_argument("--mailpit", help="адрес Mailpit развёрнутого сервиса")
    args = parser.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)

    if args.base:
        return in_browser(args.base, Mailbox(args.mailpit), args.shots)

    database_url = os.environ["KB_UI_DATABASE_URL"]
    prepare_database(database_url)
    redis_url = os.environ.get("KB_BENCH_REDIS_URL")
    if redis_url:
        # Счётчики попыток прошлых запусков не должны сработать в этом.
        redis.Redis.from_url(redis_url).flushdb()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    mailer = CapturingMailer()
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        redis_url=redis_url,
        jwt_secret="ключ-подписи-только-для-проверки-интерфейса",
        cookie_secure=False,
        public_url=base,
        log_level="WARNING",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, mailer=mailer), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)

    try:
        return in_browser(base, mailer, args.shots)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def in_browser(base: str, mailer: CapturingMailer | Mailbox, shots: Path) -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        # Сертификат развёрнутого сервиса самоподписанный.
        context = browser.new_context(
            viewport={"width": 1280, "height": 800}, locale="ru-RU", ignore_https_errors=True
        )
        page = context.new_page()
        page.set_default_timeout(10_000)
        try:
            return scenario(page, base, mailer, shots)
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
