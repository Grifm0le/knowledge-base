"""Статические файлы клиентского приложения и правила их безопасности."""

import re

from kb.api.app import WEB_DIR


def test_index_is_served_at_root(memory_client):
    response = memory_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Личная база знаний" in response.text


def test_scripts_and_styles_are_served(memory_client):
    assert memory_client.get("/app.js").status_code == 200
    assert memory_client.get("/style.css").status_code == 200


def test_pages_have_strict_policy(memory_client):
    policy = memory_client.get("/").headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy


def test_api_is_not_shadowed_by_static_files(memory_client):
    assert memory_client.get("/api/v1/health").json()["database"] == "ok"


def test_page_has_no_inline_scripts_or_handlers():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert re.search(r"<script(?![^>]*\bsrc=)", html) is None
    assert re.search(r"\son\w+=", html) is None


def test_server_markup_is_inserted_in_one_place():
    """Разметка из ответа сервера попадает на страницу только через одну проверенную функцию."""
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert script.count("innerHTML") == 1
    assert "eval(" not in script
    assert "document.write" not in script


def test_token_is_not_kept_in_browser_storage():
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "localStorage" not in script
    assert "sessionStorage" not in script
