"""Общая часть проверок развёрнутого сервиса: клиент, письма из Mailpit и вход пользователя."""

from __future__ import annotations

import re
import time

import httpx

API = "/api/v1"
LINK = re.compile(r"https?://\S+token=[\w-]+")
TOKEN_IN_LINK = re.compile(r"token=([\w-]+)")


class Mailbox:
    """Письма настоящего почтового сервера: сервис отправляет их по SMTP."""

    def __init__(self, url: str) -> None:
        self._client = httpx.Client(base_url=url, timeout=10)

    def count(self, to: str) -> int:
        response = self._client.get("/api/v1/search", params={"query": f"to:{to}"})
        return int(response.json()["messages_count"])

    def last_link(self, to: str, expected: int = 1, timeout: float = 20) -> str:
        """Ссылка из последнего письма. Письмо уходит после ответа сервиса, поэтому его ждём.

        expected: сколько писем на этот адрес должно быть к этому моменту, включая нужное.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.count(to) >= expected:
                response = self._client.get(
                    "/api/v1/search", params={"query": f"to:{to}", "limit": 1}
                )
                newest = response.json()["messages"][0]["ID"]
                text = self._client.get(f"/api/v1/message/{newest}").json()["Text"]
                match = LINK.search(text)
                if match:
                    return match.group(0)
            time.sleep(0.2)
        raise TimeoutError(f"Нет письма со ссылкой для {to}")

    def last_token(self, to: str, expected: int = 1, timeout: float = 20) -> str:
        match = TOKEN_IN_LINK.search(self.last_link(to, expected, timeout))
        assert match is not None
        return match.group(1)


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
