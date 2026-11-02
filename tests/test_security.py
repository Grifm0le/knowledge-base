"""Пароли, токены, письма и настройки безопасности без внешних зависимостей."""

import logging
import smtplib

import pytest
from pydantic import ValidationError

from conftest import JWT_SECRET, make_settings
from kb.api.app import _build_mailer
from kb.config import DatabaseSettings, Settings
from kb.errors import InvalidTokenError, WeakPasswordError
from kb.mailer import LogMailer, OutgoingMail, SmtpMailer
from kb.security import (
    ACCESS,
    REFRESH,
    TokenService,
    hash_one_time_token,
    hash_password,
    mask_email,
    needs_rehash,
    new_one_time_token,
    validate_new_password,
    verify_password,
    waste_password_check,
)


def test_same_password_gets_different_hashes():
    first, second = hash_password("пароль-для-проверки"), hash_password("пароль-для-проверки")
    assert first != second
    assert verify_password(first, "пароль-для-проверки")
    assert verify_password(second, "пароль-для-проверки")


def test_wrong_password_and_broken_hash_do_not_verify():
    hashed = hash_password("пароль-для-проверки")
    assert verify_password(hashed, "другой-пароль") is False
    assert verify_password("!", "пароль-для-проверки") is False


def test_current_hash_needs_no_upgrade():
    assert needs_rehash(hash_password("пароль-для-проверки")) is False


def test_dummy_check_runs_without_error():
    waste_password_check("любой-пароль")


@pytest.mark.parametrize(
    "password",
    [
        "надёжная-фраза-из-слов",
        "A1b2C3d4E5",
        "x" * 60 + "abcd",
    ],
)
def test_good_passwords_pass(password):
    validate_new_password(password, "anna@example.org")


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("короткий", "короче"),
        ("q" * 129, "длиннее"),
        ("Password123", "распространён"),
        ("abababababab", "разных"),
        ("xxANNAxx-2026", "адрес"),
    ],
)
def test_bad_passwords_are_explained(password, fragment):
    with pytest.raises(WeakPasswordError, match=fragment):
        validate_new_password(password, "anna@example.org")


def test_short_address_part_is_not_checked():
    validate_new_password("надёжная-фраза-ann", "an@example.org")


@pytest.fixture
def tokens():
    return TokenService(JWT_SECRET, 60, 600)


def test_token_round_trip(tokens):
    claims = tokens.decode(tokens.issue("u1", 3, ACCESS), ACCESS)
    assert (claims.user_id, claims.token_version, claims.kind) == ("u1", 3, ACCESS)


def test_tokens_are_not_interchangeable(tokens):
    with pytest.raises(InvalidTokenError):
        tokens.decode(tokens.issue("u1", 1, REFRESH), ACCESS)
    with pytest.raises(InvalidTokenError):
        tokens.decode(tokens.issue("u1", 1, ACCESS), REFRESH)


def test_two_tokens_of_one_moment_differ(tokens):
    assert tokens.issue("u1", 1, ACCESS) != tokens.issue("u1", 1, ACCESS)


def test_lifetimes_are_exposed(tokens):
    assert (tokens.access_seconds, tokens.refresh_seconds) == (60, 600)


def test_one_time_token_hash_is_stable_and_differs_from_token():
    token, stored = new_one_time_token()
    assert stored == hash_one_time_token(token)
    assert token not in stored
    assert len(stored) == 64
    assert new_one_time_token()[0] != token


@pytest.mark.parametrize(
    ("email", "masked"),
    [("anna@example.org", "a***@example.org"), ("a@b.ru", "a***@b.ru")],
)
def test_mask_email(email, masked):
    assert mask_email(email) == masked


MAIL = OutgoingMail("anna@example.org", "Тема письма", "Текст письма со ссылкой")


class FakeSmtp:
    instances: list["FakeSmtp"] = []
    fail = False

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.tls = False
        self.credentials = None
        self.message = None
        FakeSmtp.instances.append(self)

    def __enter__(self):
        if FakeSmtp.fail:
            raise smtplib.SMTPConnectError(421, "занято")
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user, password):
        self.credentials = (user, password)

    def send_message(self, message):
        self.message = message


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSmtp.instances = []
    FakeSmtp.fail = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp


def test_smtp_mailer_sends_with_tls_and_login(fake_smtp):
    SmtpMailer("mail", 587, "noreply@example.org", "user", "secret").send(MAIL)
    client = fake_smtp.instances[0]
    assert (client.host, client.port) == ("mail", 587)
    assert client.tls is True
    assert client.credentials == ("user", "secret")
    assert client.message["To"] == "anna@example.org"
    assert client.message["Subject"] == "Тема письма"
    assert "Текст письма" in client.message.get_content()


def test_smtp_mailer_can_work_without_tls_and_login(fake_smtp):
    SmtpMailer("mail", 1025, "noreply@example.org", starttls=False).send(MAIL)
    client = fake_smtp.instances[0]
    assert client.tls is False
    assert client.credentials is None


def test_smtp_failure_is_logged_without_full_address(fake_smtp, caplog):
    fake_smtp.fail = True
    with caplog.at_level(logging.ERROR, logger="kb.mailer"):
        SmtpMailer("mail", 587, "noreply@example.org").send(MAIL)
    assert "a***@example.org" in caplog.text
    assert "anna@example.org" not in caplog.text


def test_log_mailer_masks_address(caplog):
    with caplog.at_level(logging.WARNING, logger="kb.mailer"):
        LogMailer().send(MAIL)
    assert "a***@example.org" in caplog.text
    assert "anna@example.org" not in caplog.text
    assert "Текст письма" in caplog.text


def test_settings_need_jwt_secret(monkeypatch):
    monkeypatch.delenv("KB_JWT_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_short_jwt_secret_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret="короткий")


def test_secret_is_hidden_in_settings_text():
    settings = Settings(_env_file=None, jwt_secret=JWT_SECRET, smtp_password="пароль-почты")
    assert JWT_SECRET not in repr(settings)
    assert "пароль-почты" not in repr(settings)


def test_migrations_settings_do_not_need_secret(monkeypatch):
    monkeypatch.delenv("KB_JWT_SECRET", raising=False)
    assert DatabaseSettings(_env_file=None).database_url


def test_mailer_is_chosen_from_settings():
    assert isinstance(_build_mailer(make_settings()), LogMailer)
    configured = make_settings(smtp_host="mail", smtp_user="user", smtp_password="секрет")
    assert isinstance(_build_mailer(configured), SmtpMailer)


def test_request_id_from_client_is_checked(memory_client):
    good = memory_client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert good.headers["X-Request-ID"] == "abc-123"
    bad = memory_client.get("/api/v1/health", headers={"X-Request-ID": "x" * 65})
    assert bad.headers["X-Request-ID"] != "x" * 65
    strange = memory_client.get("/api/v1/health", headers={"X-Request-ID": "a b;c"})
    assert strange.headers["X-Request-ID"] != "a b;c"
