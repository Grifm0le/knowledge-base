"""Снимок письма в Mailpit: python tools/shot_mailpit.py [адрес Mailpit] [файл]."""

from __future__ import annotations

import sys

import httpx
from playwright.sync_api import sync_playwright


def main(url: str, target: str) -> None:
    messages = httpx.get(f"{url}/api/v1/search", params={"query": "subject:Восстановление"}).json()
    newest = messages["messages"][0]["ID"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(
            viewport={"width": 1100, "height": 560}, locale="ru-RU", device_scale_factor=2
        )
        page.goto(f"{url}/view/{newest}")
        page.wait_for_selector("#nav-plain-text-tab, #nav-plain-text, .tab-content", timeout=10_000)
        page.wait_for_timeout(800)
        # Только текст письма: заголовки почтового сервера здесь не важны.
        page.screenshot(path=target, clip={"x": 0, "y": 252, "width": 1000, "height": 76})
        browser.close()


if __name__ == "__main__":
    main(
        sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8025",
        sys.argv[2] if len(sys.argv) > 2 else "docs/img/mailpit.png",
    )
