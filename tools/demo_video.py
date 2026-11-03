"""Запись демонстрации работы сервиса: python tools/demo_video.py [--out docs/demo/demo.mp4].

Открывает Microsoft Edge без окна и проходит настоящий сценарий на развёрнутом сервисе: регистрация,
письмо, импорт, поиск всеми методами, версии, совместный доступ, статистика и Swagger UI. Голоса в
видео нет, пояснения показываются подписями внизу кадра. Кадры снимаются через CDP и кодируются в
H.264 программой ffmpeg из пакета imageio-ffmpeg.
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import time
import uuid
from html import escape
from pathlib import Path

import httpx
import imageio_ffmpeg
from playwright.sync_api import CDPSession, Locator, Page, expect, sync_playwright
from stack_client import API, Mailbox, make_client, new_user

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
BAD_FILES = ROOT / "tests" / "data" / "import_set"
WIDTH, HEIGHT = 1280, 720
FPS = 15
PASSWORD = "надёжная-фраза-для-показа"
TAG = uuid.uuid4().hex[:4]
FIRST = f"anna.{TAG}@example.org"
SECOND = f"boris.{TAG}@example.org"

CAPTION = """(text) => {
  let box = document.getElementById("demo-caption");
  if (!box) {
    box = document.createElement("div");
    box.id = "demo-caption";
    box.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:99999;padding:14px 32px;"
      + "background:rgba(15,23,42,.93);color:#fff;font:600 21px/1.35 'Segoe UI',sans-serif;"
      + "text-align:center;pointer-events:none";
    document.documentElement.appendChild(box);
    document.body.style.paddingBottom = "96px";
  }
  box.textContent = text;
}"""
HIGHLIGHT = """(element) => {
  element.style.outline = "3px solid #f97316";
  element.style.outlineOffset = "2px";
  setTimeout(() => { element.style.outline = ""; }, 800);
}"""
CARD = """<!doctype html><meta charset="utf-8"><style>
body {{ margin: 0; height: 100vh; display: flex; flex-direction: column; justify-content: center;
  padding: 0 90px; box-sizing: border-box; background: #0f172a; color: #e2e8f0;
  font-family: 'Segoe UI', sans-serif; }}
h1 {{ font-size: 50px; margin: 0 0 18px; color: #fff; }}
p {{ font-size: 25px; line-height: 1.5; margin: 6px 0; }}
pre {{ font: 19px/1.55 Consolas, monospace; background: #020617; color: #a7f3d0; padding: 22px 28px;
  border-radius: 8px; margin: 18px 0 0; }}
</style>{body}"""


class Recorder:
    """Снимает кадры страницы через CDP и собирает из них видео с постоянной частотой кадров."""

    def __init__(self, page: Page) -> None:
        self.frames: list[tuple[float, bytes]] = []
        self.session: CDPSession = page.context.new_cdp_session(page)
        self.session.on("Page.screencastFrame", self._frame)
        self.finished_at = 0.0

    def _frame(self, event: dict) -> None:
        self.frames.append((event["metadata"]["timestamp"], base64.b64decode(event["data"])))
        self.session.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def start(self) -> None:
        self.session.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 92, "maxWidth": WIDTH, "maxHeight": HEIGHT},
        )

    def stop(self) -> None:
        self.finished_at = time.time()
        self.session.send("Page.stopScreencast")

    def encode(self, target: Path) -> float:
        target.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = subprocess.Popen(  # noqa: S603
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-loglevel", "error",
                "-f", "image2pipe",
                "-framerate", str(FPS),
                "-c:v", "mjpeg",
                "-i", "-",
                "-vf", f"scale={WIDTH}:{HEIGHT}:flags=lanczos,format=yuv420p",
                "-c:v", "libx264",
                "-preset", "slow",
                "-crf", "24",
                "-movflags", "+faststart",
                str(target),
            ],
            stdin=subprocess.PIPE,
        )  # fmt: skip
        assert ffmpeg.stdin is not None
        started = self.frames[0][0]
        duration = self.finished_at - started
        current = 0
        for number in range(int(duration * FPS)):
            moment = started + number / FPS
            while current + 1 < len(self.frames) and self.frames[current + 1][0] <= moment:
                current += 1
            ffmpeg.stdin.write(self.frames[current][1])
        ffmpeg.stdin.close()
        if ffmpeg.wait() != 0:
            raise RuntimeError("ffmpeg завершился с ошибкой")
        return duration


class Demo:
    """Действия с паузами и подписями: зритель успевает понять, что происходит на экране."""

    def __init__(self, page: Page, base: str) -> None:
        self.page = page
        self.base = base

    def say(self, text: str) -> None:
        self.page.evaluate(CAPTION, text)

    def wait(self, seconds: float) -> None:
        self.page.wait_for_timeout(seconds * 1000)

    def open(self, route: str) -> None:
        self.page.goto(f"{self.base}/#/{route}")

    def point(self, locator: Locator) -> None:
        locator.scroll_into_view_if_needed()
        locator.evaluate(HIGHLIGHT)
        self.wait(0.5)

    def click(self, target: str | Locator) -> None:
        locator = self.page.locator(target) if isinstance(target, str) else target
        self.point(locator)
        locator.click()

    def type(self, selector: str, text: str) -> None:
        locator = self.page.locator(selector)
        locator.fill("")
        locator.press_sequentially(text, delay=55)

    def card(self, body: str, seconds: float) -> None:
        self.page.goto("about:blank")
        self.page.set_content(CARD.format(body=body))
        self.wait(seconds)

    def search(self, query: str | None = None) -> None:
        if query is not None:
            self.type("input[name=q]", query)
        self.click("#page button[type=submit], form button[type=submit]")
        expect(self.page.locator("#total")).to_contain_text("Найдено")

    def scorer(self, value: str) -> None:
        self.page.select_option("select[name=scorer]", value)


def title_scenes(demo: Demo, stack_status: str) -> None:
    demo.card(
        "<h1>Личная база знаний</h1>"
        "<p>Сервис для заметок в формате Markdown: импорт, анализ, скоринг и поиск.</p>"
        "<p>Python, FastAPI, PostgreSQL, Redis, Docker Compose.</p>"
        "<p>Кейс VK Education Projects. Демонстрация на развёрнутом сервисе.</p>",
        5,
    )
    demo.card(
        "<h1>Запуск в Docker Compose</h1>"
        "<p>sh tools/init_env.sh, sh tools/make_cert.sh,</p>"
        "<p>docker compose --profile demo up -d --build</p>"
        f"<pre>{escape(stack_status)}</pre>",
        6,
    )


def account_scenes(demo: Demo, mailbox: Mailbox, mailpit: str) -> None:
    page = demo.page
    demo.open("register")
    demo.say("Регистрация. Слабый пароль отклоняется, причина показана сразу")
    demo.type("input[name=email]", FIRST)
    demo.type("input[name=password]", "короткий")
    page.check("input[name=consent]")
    demo.click("button[type=submit]")
    expect(page.locator("#message")).to_contain_text("короче")
    demo.wait(3)

    demo.say("С надёжным паролем регистрация принята, на почту уходит письмо")
    demo.type("input[name=password]", PASSWORD)
    demo.click("button[type=submit]")
    expect(page.locator("#message")).to_contain_text("отправлено письмо")
    demo.wait(3)

    link = mailbox.last_link(FIRST)
    with httpx.Client(base_url=mailpit, timeout=10) as client:
        found = client.get("/api/v1/search", params={"query": f"to:{FIRST}", "limit": 1})
    page.goto(f"{mailpit}/view/{found.json()['messages'][0]['ID']}")
    demo.say("Письмо с разовой ссылкой, почтовый сервер Mailpit входит в демонстрационный стек")
    demo.wait(5)

    page.goto(link)
    expect(page.locator("#message")).to_contain_text("Адрес подтверждён")
    demo.say("Адрес подтверждён по ссылке из письма")
    demo.wait(3)

    demo.open("login")
    demo.say("Вход: сервис выдаёт токен доступа JWT")
    demo.type("input[name=email]", FIRST)
    demo.type("input[name=password]", PASSWORD)
    demo.click("button[type=submit]")
    expect(page.locator("#user-email")).to_have_text(FIRST)
    expect(page.locator("h1")).to_have_text("Поиск")
    demo.wait(2)


def import_scenes(demo: Demo) -> None:
    page = demo.page
    demo.open("documents")
    demo.say("Импорт: загружаем файлы Markdown. Сервис разбирает шапку, заголовки, код и ссылки")
    expect(page.locator("#drop")).to_be_visible()
    demo.wait(3)
    page.set_input_files("#file-input", sorted(str(path) for path in SAMPLES.glob("*.md")))
    expect(page.locator("#message")).to_contain_text("Создано: 8")
    expect(page.locator("#documents tbody tr").first).to_be_visible()
    demo.say("Создано 8 документов. Заметка без шапки тоже принята: название взято из заголовка")
    demo.wait(5)

    demo.say(
        "Файлы с ошибками: неверная шапка и кодировка. Каждый файл получает свою причину отказа"
    )
    page.set_input_files(
        "#file-input", [str(BAD_FILES / "bad_yaml.md"), str(BAD_FILES / "cp1251.md")]
    )
    expect(page.locator("#message")).to_contain_text("отклонено: 2")
    demo.point(page.locator("#message"))
    demo.wait(6)


def search_scenes(demo: Demo, poster: Path) -> None:
    page = demo.page
    demo.open("search")
    demo.say(
        "Полнотекстовый поиск с учётом морфологии: совпадения подсвечены, у результата есть оценка"
    )
    demo.search("индексы для поиска")
    expect(page.locator(".hit mark").first).to_be_visible()
    demo.wait(3)
    page.screenshot(path=poster)
    demo.wait(2)

    demo.say(
        "Другая форма слова тоже находит документы: «индексами» совпало с «индекс» и «индекса»"
    )
    demo.search("индексами")
    expect(page.locator(".hit mark").first).to_be_visible()
    demo.wait(4)

    demo.say("TF-IDF: оценки и порядок считает другой метод")
    demo.scorer("tfidf")
    demo.search()
    demo.wait(4)

    demo.say("PostgreSQL FTS: оценки выдаёт сама база данных по индексу GIN")
    demo.scorer("pgfts")
    demo.search()
    demo.wait(4)

    demo.say("Гибридный метод: веса полей и вклад методов задаются прямо в запросе")
    demo.scorer("hybrid")
    demo.click("#weights summary")
    page.locator("input[name=w_title]").fill("10")
    page.locator("input[name=hybrid_bm25]").fill("0.5")
    page.locator("input[name=hybrid_tfidf]").fill("0.5")
    demo.wait(2)
    demo.search()
    demo.wait(4)

    demo.say("Повторный запрос приходит из кэша Redis, это видно рядом с названием метода")
    demo.search()
    expect(page.locator("#results")).to_contain_text("из кэша")
    demo.point(page.locator("#results .row.spread").first)
    demo.wait(4)

    demo.open("search")
    demo.say("Фильтры по тегам, категории, автору и дате, сортировка")
    demo.click("details:has(input[name=tag]) summary")
    demo.type("input[name=tag]", "производительность")
    page.select_option("select[name=sort]", "title")
    demo.search("кэш")
    expect(page.locator(".hit").first).to_be_visible()
    demo.wait(5)


def document_scenes(demo: Demo) -> str:
    page = demo.page
    demo.click(page.locator("a.hit-title", has_text="Кэширование в Redis"))
    expect(page.locator("#doc-title")).to_have_text("Кэширование в Redis")
    demo.say("Просмотр документа: текст из Markdown, метаданные и показатели")
    demo.wait(4)
    url = page.url

    demo.click("button[data-tab=edit]")
    demo.say("Правка в редакторе. Сохранение создаёт новую версию, старая остаётся в истории")
    editor = page.locator("#editor")
    editor.click()
    page.keyboard.press("Control+End")
    page.keyboard.type("\nПосле правки выдача сбрасывается сразу.\n", delay=45)
    demo.wait(1)
    demo.click("#tab-content button[type=submit]")
    expect(page.locator("#doc-version")).to_have_text("версия 2")
    demo.wait(3)

    demo.click("button[data-tab=versions]")
    demo.say("История версий и сравнение: добавленная строка отмечена цветом")
    demo.click("#diff-run")
    expect(page.locator(".diff .add").first).to_be_visible()
    demo.point(page.locator(".diff .add").first)
    demo.wait(5)

    demo.say("Старую версию можно вернуть: она сохранится как новая, история не теряется")
    demo.click("button[data-restore='1']")
    expect(page.locator("#doc-version")).to_have_text("версия 3")
    demo.wait(4)
    return url


def stats_scene(demo: Demo) -> None:
    demo.open("stats")
    expect(demo.page.locator("#stat-cards")).to_be_visible()
    demo.say("Статистика: число документов, размер базы, теги и категории")
    demo.wait(6)


def sharing_scenes(demo: Demo, doc_url: str) -> None:
    page = demo.page
    page.goto(doc_url)
    demo.say("Совместный доступ: владелец открывает документ другому пользователю на чтение")
    demo.click("button[data-tab=shares]")
    demo.type("input[name=share-email]", SECOND)
    demo.click("#tab-content button[type=submit]")
    expect(page.locator("#message")).to_contain_text("Доступ открыт")
    demo.wait(4)

    demo.click("#logout")
    expect(page.locator("h1")).to_have_text("Вход")
    demo.say("Входим как второй пользователь. У него своя база")
    demo.type("input[name=email]", SECOND)
    demo.type("input[name=password]", PASSWORD)
    demo.click("button[type=submit]")
    expect(page.locator("#user-email")).to_have_text(SECOND)

    demo.say("Чужие заметки ему не видны: поиск по ним ничего не находит")
    demo.search("pytest")
    expect(page.locator("#total")).to_have_text("Найдено: 0")
    demo.wait(4)

    page.goto(doc_url)
    expect(page.locator("#doc-title")).to_have_text("Кэширование в Redis")
    expect(page.locator("button[data-tab=edit]")).to_have_count(0)
    demo.say("Открытый ему документ доступен только на чтение: вкладки правки нет")
    demo.wait(5)


def swagger_scene(demo: Demo) -> None:
    page = demo.page
    page.goto(f"{demo.base}/docs")
    expect(page.locator(".opblock").first).to_be_visible()
    demo.say("REST API: 27 методов, документация Swagger UI формируется из кода")
    demo.wait(3)
    for _ in range(9):
        page.mouse.wheel(0, 240)
        demo.wait(0.6)
    demo.wait(1)


def final_scene(demo: Demo) -> None:
    demo.card(
        "<h1>Итоги проверки</h1>"
        "<p>569 тестов на настоящих PostgreSQL и Redis, покрытие кода 99 %.</p>"
        "<p>Сценарий в браузере: 26 шагов. Проверка защиты: 52. Проверка сбоев: 16. Ошибок нет.</p>"
        "<p>2000 документов: около 100 запросов в секунду, из кэша около 210.</p>"
        "<p>Код, инструкции и документация находятся в репозитории.</p>",
        7,
    )


def scenario(page: Page, base: str, mailpit: str, status: str, poster: Path, rec: Recorder) -> None:
    mailbox = Mailbox(mailpit)
    new_user(base, mailbox, SECOND, PASSWORD)
    demo = Demo(page, base)
    page.goto("about:blank")
    rec.start()
    title_scenes(demo, status)
    account_scenes(demo, mailbox, mailpit)
    import_scenes(demo)
    search_scenes(demo, poster)
    doc_url = document_scenes(demo)
    stats_scene(demo)
    sharing_scenes(demo, doc_url)
    swagger_scene(demo)
    final_scene(demo)
    rec.stop()


def delete_users(base: str) -> None:
    """Учётные записи показа не нужны после записи: удаляются вместе с документами."""
    with make_client(base) as client:
        for email in (FIRST, SECOND):
            login = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
            login.raise_for_status()
            client.post(
                f"{API}/auth/me/delete",
                json={"password": PASSWORD},
                headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            ).raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://127.0.0.1:8443")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "demo" / "demo.mp4")
    parser.add_argument("--poster", type=Path, default=ROOT / "docs" / "demo" / "poster.png")
    parser.add_argument("--status", type=Path, default=ROOT / "runs" / "80_demo_stack.txt")
    args = parser.parse_args()
    args.poster.parent.mkdir(parents=True, exist_ok=True)
    status = args.status.read_text(encoding="utf-8").strip()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            locale="ru-RU",
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(15_000)
        problems: list[str] = []
        page.on("pageerror", lambda error: problems.append(str(error)))
        recorder = Recorder(page)
        try:
            scenario(page, args.base, args.mailpit, status, args.poster, recorder)
        finally:
            browser.close()

    duration = recorder.encode(args.out)
    delete_users(args.base)
    size = args.out.stat().st_size / 1024 / 1024
    print(f"Видео: {args.out.name}, {duration:.0f} с, {size:.1f} МБ, кадров {len(recorder.frames)}")
    print(f"Ошибок скриптов на странице: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
