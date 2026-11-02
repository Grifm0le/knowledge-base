"""Общая часть проверок запущенного сервиса: клиент, письма из Mailpit и вход пользователя."""

from __future__ import annotations

import re
import time

import httpx

API = "/api/v1"
TOKEN_IN_LINK = re.compile(r"token=([\w-]+)")


class Mailbox:
    """Письма настоящего почтового сервера: сервис отправляет их по SMTP."""

    def __init__(self, url: str) -> None:
        self._client = httpx.Client(base_url=url, timeout=10)

    def last_token(self, to: str, timeout: float = 15) -> str:
        """Разовый токен из последнего письма на адрес. Письмо уходит уже после ответа сервиса."""
        deadline = time.monotonic() + timeout
        seen = self._newest_id(to)
        while time.monotonic() < deadline:
            current = self._newest_id(to)
            if current is not None and (seen is None or current != seen):
                seen = current
            if current is not None:
                text = self._client.get(f"/api/v1/message/{current}").json()["Text"]
                match = TOKEN_IN_LINK.search(text)
                if match:
                    return match.group(1)
            time.sleep(0.2)
        raise TimeoutError(f"Нет письма с токеном для {to}")

    def wait_new_token(self, to: str, previous: str, timeout: float = 15) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            token = self.last_token(to)
            if token != previous:
                return token
            time.sleep(0.2)
        raise TimeoutError(f"Нет нового письма для {to}")

    def count(self, to: str) -> int:
        response = self._client.get("/api/v1/search", params={"query": f"to:{to}"})
        return int(response.json()["messages_count"])

    def _newest_id(self, to: str) -> str | None:
        response = self._client.get("/api/v1/search", params={"query": f"to:{to}", "limit": 1})
        messages = response.json()["messages"]
        return messages[0]["ID"] if messages else None


def make_client(base_url: str, token: str | None = None) -> httpx.Client:
    """Клиент для сервиса. Сертификат самоподписанный, поэтому его подлинность не проверяется."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=base_url, headers=headers, verify=False, timeout=60)  # noqa: S501


def new_user(base_url: str, mailbox: Mailbox, email: str, password: str) -> str:
    """Регистрирует пользователя, подтверждает адрес по письму и возвращает токен доступа."""
    with make_client(base_url) as client:
        response = client.post(
            f"{API}/auth/register", json={"email": email, "password": password, "consent": True}
        )
        response.raise_for_status()
        token = mailbox.last_token(email)
        client.post(f"{API}/auth/verify-email", json={"token": token}).raise_for_status()
        login = client.post(f"{API}/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        return str(login.json()["access_token"])
