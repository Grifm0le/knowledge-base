"""Отправка писем: подтверждение адреса и восстановление пароля."""

from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage

from kb.security import mask_email

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    to: str
    subject: str
    body: str


class Mailer(ABC):
    @abstractmethod
    def send(self, mail: OutgoingMail) -> None:
        """Отправляет письмо."""


class LogMailer(Mailer):
    """Режим разработки без почтового сервера: письмо с разовой ссылкой пишется в журнал."""

    def send(self, mail: OutgoingMail) -> None:
        logger.warning("SMTP не настроен. Письмо для %s:\n%s", mask_email(mail.to), mail.body)


class SmtpMailer(Mailer):
    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        user: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        timeout_seconds: float = 10,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._user = user
        self._password = password
        self._starttls = starttls
        self._timeout = timeout_seconds

    def send(self, mail: OutgoingMail) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message.set_content(mail.body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
                if self._starttls:
                    client.starttls()
                if self._user and self._password:
                    client.login(self._user, self._password)
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            # Письмо уходит после ответа клиенту, поэтому ошибку можно только записать в журнал.
            logger.error("Письмо для %s не отправлено: %s", mask_email(mail.to), exc)
