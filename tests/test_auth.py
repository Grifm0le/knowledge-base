"""Вход через API: каждый сценарий проверяется и на памяти, и на настоящем PostgreSQL."""

import logging
import re
from datetime import datetime, timezone

import jwt
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from conftest import JWT_SECRET, PASSWORD, make_settings
from fakes import InMemoryRepository, InMemoryUserRepository, MemoryMailer
from kb.api.app import create_app
from kb.cache import MemoryCache
from kb.errors import InvalidTokenError
from kb.security import ACCESS, REFRESH, TokenService, hash_one_time_token

AUTH = "/api/v1/auth"
EMAIL = "anna@example.org"
NEW_PASSWORD = "другой-надёжный-пароль-2026"
TOKEN_IN_LINK = re.compile(r"token=([\w-]+)")


class Api:
    """Клиент без токена, письма и хранилище учётных записей одного теста."""

    def __init__(self, client, mailer, users):
        self.client = client
        self.mailer = mailer
        self.users = users
        client.headers.pop("Authorization", None)

    def register(self, email=EMAIL, password=PASSWORD, consent=True):
        body = {"email": email, "password": password, "consent": consent}
        return self.client.post(f"{AUTH}/register", json=body)

    def login(self, email=EMAIL, password=PASSWORD):
        return self.client.post(f"{AUTH}/login", json={"email": email, "password": password})

    def last_token(self):
        return TOKEN_IN_LINK.search(self.mailer.sent[-1].body).group(1)

    def verified(self, email=EMAIL, password=PASSWORD):
        assert self.register(email, password).status_code == 202
        token = self.last_token()
        assert self.client.post(f"{AUTH}/verify-email", json={"token": token}).status_code == 204

    def access_token(self, email=EMAIL, password=PASSWORD):
        self.verified(email, password)
        return self.login(email, password).json()["access_token"]

    def me(self, token):
        return self.client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {token}"})


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
def api(request, client_for):
    mailer = MemoryMailer()
    if request.param == "postgres":
        users = request.getfixturevalue("users")
        client = client_for(request.getfixturevalue("repository"), users=users, mailer=mailer)
    else:
        users = InMemoryUserRepository()
        client = client_for(InMemoryRepository(), users=users, mailer=mailer)
    return Api(client, mailer, users)


def error_code(response):
    return response.json()["error"]["code"]


def test_register_sends_link_and_stores_no_plain_password(api):
    response = api.register()
    assert response.status_code == 202
    assert api.mailer.sent[-1].to == EMAIL
    assert "/#/verify?token=" in api.mailer.sent[-1].body
    stored = api.users.find_by_email(EMAIL)
    assert stored.password_hash.startswith("$argon2id$")
    assert PASSWORD not in stored.password_hash
    assert stored.email_verified_at is None
    assert stored.consent_at is not None


def test_taken_address_gets_same_answer_and_a_notice(api):
    api.verified()
    first = api.register("free@example.org")
    second = api.register(EMAIL)
    assert (second.status_code, second.json()) == (first.status_code, first.json())
    assert api.mailer.sent[-1].to == EMAIL
    assert "уже занят" in api.mailer.sent[-1].body
    assert "token=" not in api.mailer.sent[-1].body


def test_repeated_registration_of_unverified_address_gives_new_link(api):
    api.register()
    old = api.last_token()
    api.register()
    new = api.last_token()
    assert new != old
    assert api.client.post(f"{AUTH}/verify-email", json={"token": old}).status_code == 400
    assert api.client.post(f"{AUTH}/verify-email", json={"token": new}).status_code == 204


def test_address_is_case_insensitive(api):
    api.verified("Anna@Example.org")
    assert api.login("ANNA@example.org").status_code == 200


def test_registration_without_consent_is_rejected(api):
    response = api.register(consent=False)
    assert response.status_code == 422
    assert error_code(response) == "consent_required"
    assert api.users.find_by_email(EMAIL) is None


@pytest.mark.parametrize(
    "password",
    ["короткий", "qwerty12345", "aaaaabbbbbaaaaa", "x" * 129, "anna-и-ещё-слова"],
    ids=["short", "common", "few-symbols", "long", "contains-address"],
)
def test_weak_password_is_rejected(api, password):
    response = api.register(password=password)
    assert response.status_code == 422
    assert error_code(response) == "weak_password"
    assert api.users.find_by_email(EMAIL) is None


@pytest.mark.parametrize("email", ["без-собаки", "a@", "@example.org", "a b@example.org"])
def test_invalid_address_is_rejected(api, email):
    response = api.register(email)
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"


def test_validation_error_does_not_repeat_password(api):
    response = api.client.post(
        f"{AUTH}/register", json={"email": "плохой", "password": PASSWORD, "consent": True}
    )
    assert PASSWORD not in response.text


def test_login_before_verification_is_refused_only_with_right_password(api):
    api.register()
    assert error_code(api.login()) == "email_not_verified"
    assert api.login().status_code == 403
    assert error_code(api.login(password="неверный-пароль-1")) == "invalid_credentials"


def test_verification_link_works_once(api):
    api.register()
    token = api.last_token()
    assert api.client.post(f"{AUTH}/verify-email", json={"token": token}).status_code == 204
    again = api.client.post(f"{AUTH}/verify-email", json={"token": token})
    assert again.status_code == 400
    assert error_code(again) == "invalid_one_time_token"


def test_unknown_verification_token_is_rejected(api):
    response = api.client.post(f"{AUTH}/verify-email", json={"token": "выдуманный"})
    assert response.status_code == 400


def test_one_time_token_is_stored_as_hash(api):
    api.register()
    token = api.last_token()
    now = datetime.now(timezone.utc)
    assert api.users.find_one_time_token(token, "verify_email", now) is None
    assert api.users.find_one_time_token(hash_one_time_token(token), "verify_email", now)


def test_login_gives_token_and_protected_cookie(api):
    api.verified()
    response = api.login()
    body = response.json()
    assert response.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 30 * 60
    cookie = response.headers["set-cookie"]
    assert "kb_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert body["access_token"] != cookie


def test_cookie_is_secure_when_configured(client_for):
    mailer = MemoryMailer()
    client = client_for(InMemoryRepository(), mailer=mailer, cookie_secure=True)
    api = Api(client, mailer, None)
    api.verified()
    assert "Secure" in api.login().headers["set-cookie"]


def test_token_holds_no_personal_data(api):
    claims = jwt.decode(api.access_token(), options={"verify_signature": False})
    assert set(claims) == {"sub", "tv", "typ", "iat", "exp", "jti"}
    assert EMAIL not in str(claims)


def test_me_returns_stored_data_without_hash(api):
    response = api.me(api.access_token())
    assert response.status_code == 200
    assert response.json()["email"] == EMAIL
    assert "password" not in response.text
    assert "argon2" not in response.text


def test_wrong_password_and_unknown_address_look_the_same(api):
    api.verified()
    wrong = api.login(password="неверный-пароль-1")
    unknown = api.login("nobody@example.org")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_is_blocked_after_five_failures(api):
    api.verified()
    for _ in range(5):
        assert api.login(password="неверный-пароль-1").status_code == 401
    blocked = api.login()
    assert blocked.status_code == 429
    assert error_code(blocked) == "too_many_requests"
    assert blocked.headers["Retry-After"] == str(15 * 60)


def test_success_resets_failure_counter(api):
    api.verified()
    for _ in range(4):
        api.login(password="неверный-пароль-1")
    assert api.login().status_code == 200
    for _ in range(4):
        assert api.login(password="неверный-пароль-1").status_code == 401
    assert api.login().status_code == 200


def test_login_guessing_from_one_address_is_blocked(api):
    for number in range(30):
        assert api.login(f"user{number}@example.org").status_code == 401
    assert api.login("another@example.org").status_code == 429


def test_registrations_from_one_address_are_limited(api):
    for number in range(10):
        assert api.register(f"user{number}@example.org").status_code == 202
    assert api.register("another@example.org").status_code == 429


def test_old_hash_is_upgraded_on_login(api):
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    api.verified()
    user = api.users.find_by_email(EMAIL)
    api.users.update_password_hash(user.id, weak)
    assert api.login().status_code == 200
    assert api.users.find_by_email(EMAIL).password_hash != weak


@pytest.mark.parametrize(
    "header",
    [None, "Bearer", "Bearer abc", "Basic abc"],
    ids=["none", "empty", "garbage", "other-scheme"],
)
def test_protected_method_requires_token(api, header):
    headers = {"Authorization": header} if header else {}
    response = api.client.get("/api/v1/documents", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("post", "/api/v1/documents"),
        ("post", "/api/v1/documents/import"),
        ("get", "/api/v1/documents"),
        ("get", "/api/v1/search"),
        ("get", "/api/v1/stats"),
        ("get", "/api/v1/documents/00000000-0000-0000-0000-000000000000"),
        ("get", "/api/v1/documents/00000000-0000-0000-0000-000000000000/preview"),
        ("get", "/api/v1/documents/00000000-0000-0000-0000-000000000000/shares"),
        ("get", "/api/v1/documents/00000000-0000-0000-0000-000000000000/versions"),
        ("get", "/api/v1/documents/00000000-0000-0000-0000-000000000000/diff"),
        ("get", f"{AUTH}/me"),
        ("post", f"{AUTH}/logout"),
    ],
)
def test_every_data_method_is_closed_without_token(api, method, url):
    assert getattr(api.client, method)(url).status_code == 401


def test_health_stays_public(api):
    assert api.client.get("/api/v1/health").status_code == 200


def forged(claims, secret=JWT_SECRET, algorithm="HS256"):
    return jwt.encode(claims, secret, algorithm=algorithm)


def test_token_signed_with_other_key_is_rejected(api):
    claims = jwt.decode(api.access_token(), options={"verify_signature": False})
    assert (
        api.me(forged(claims, "другой-ключ-подписи-длиной-больше-32-символов")).status_code == 401
    )


def test_changed_token_is_rejected(api):
    token = api.access_token()
    head, body, signature = token.split(".")
    assert api.me(f"{head}.{body}.{signature[::-1]}").status_code == 401


def test_token_without_signature_is_rejected(api):
    claims = jwt.decode(api.access_token(), options={"verify_signature": False})
    assert api.me(jwt.encode(claims, key=None, algorithm="none")).status_code == 401


def test_token_with_other_algorithm_is_rejected(api):
    claims = jwt.decode(api.access_token(), options={"verify_signature": False})
    assert api.me(forged(claims, algorithm="HS512")).status_code == 401


def test_expired_token_is_rejected(api):
    claims = jwt.decode(api.access_token(), options={"verify_signature": False})
    claims["exp"] = claims["iat"] - 1
    response = api.me(forged(claims))
    assert response.status_code == 401
    assert error_code(response) == "invalid_token"


def test_token_of_unknown_user_is_rejected(api):
    claims = jwt.decode(api.access_token(), options={"verify_signature": False})
    claims["sub"] = "00000000-0000-4000-8000-00000000ffff"
    assert api.me(forged(claims)).status_code == 401


def test_refresh_token_does_not_open_data_methods(api):
    api.verified()
    refresh = api.login().cookies["kb_refresh"]
    assert api.me(refresh).status_code == 401


def test_refresh_gives_new_working_token(api):
    api.verified()
    api.login()
    response = api.client.post(f"{AUTH}/refresh")
    assert response.status_code == 200
    assert api.me(response.json()["access_token"]).status_code == 200


def test_refresh_without_cookie_is_rejected(api):
    assert api.client.post(f"{AUTH}/refresh").status_code == 401


def test_access_token_does_not_refresh(api):
    token = api.access_token()
    api.client.cookies.clear()
    api.client.cookies.set("kb_refresh", token)
    assert api.client.post(f"{AUTH}/refresh").status_code == 401


def test_logout_revokes_all_tokens_and_clears_cookie(api):
    token = api.access_token()
    refresh = api.client.cookies["kb_refresh"]
    response = api.client.post(f"{AUTH}/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204
    assert 'kb_refresh=""' in response.headers["set-cookie"]
    assert api.me(token).status_code == 401
    api.client.cookies.set("kb_refresh", refresh)
    assert api.client.post(f"{AUTH}/refresh").status_code == 401


def change_password(api, token, current=PASSWORD, new=NEW_PASSWORD):
    return api.client.post(
        f"{AUTH}/password/change",
        json={"current_password": current, "new_password": new},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_change_password_replaces_tokens(api):
    old_token = api.access_token()
    response = change_password(api, old_token)
    assert response.status_code == 200
    assert api.me(old_token).status_code == 401
    assert api.me(response.json()["access_token"]).status_code == 200
    assert api.login().status_code == 401
    assert api.login(password=NEW_PASSWORD).status_code == 200


def test_change_password_needs_current_password(api):
    token = api.access_token()
    assert change_password(api, token, current="неверный-пароль-1").status_code == 401
    assert api.me(token).status_code == 200


def test_change_password_checks_new_password(api):
    token = api.access_token()
    response = change_password(api, token, new="короткий")
    assert response.status_code == 422
    assert error_code(response) == "weak_password"
    assert api.login().status_code == 200


def forgot(api, email=EMAIL):
    return api.client.post(f"{AUTH}/password/forgot", json={"email": email})


def reset(api, token, password=NEW_PASSWORD):
    return api.client.post(
        f"{AUTH}/password/reset", json={"token": token, "new_password": password}
    )


def test_forgot_answers_the_same_for_any_address(api):
    api.verified()
    sent = len(api.mailer.sent)
    known = forgot(api)
    assert len(api.mailer.sent) == sent + 1
    unknown = forgot(api, "nobody@example.org")
    assert len(api.mailer.sent) == sent + 1
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    assert known.status_code == 202


def test_reset_changes_password_and_revokes_tokens(api):
    token = api.access_token()
    forgot(api)
    assert "/#/reset?token=" in api.mailer.sent[-1].body
    assert reset(api, api.last_token()).status_code == 204
    assert api.me(token).status_code == 401
    assert api.login().status_code == 401
    assert api.login(password=NEW_PASSWORD).status_code == 200


def test_reset_link_works_once(api):
    api.verified()
    forgot(api)
    link = api.last_token()
    assert reset(api, link).status_code == 204
    again = reset(api, link, "третий-надёжный-пароль-2026")
    assert again.status_code == 400
    assert error_code(again) == "invalid_one_time_token"
    assert api.login(password=NEW_PASSWORD).status_code == 200


def test_new_reset_link_cancels_previous(api):
    api.verified()
    forgot(api)
    old = api.last_token()
    forgot(api)
    assert reset(api, old).status_code == 400
    assert reset(api, api.last_token()).status_code == 204


def test_weak_password_does_not_spend_reset_link(api):
    api.verified()
    forgot(api)
    link = api.last_token()
    response = reset(api, link, "короткий")
    assert response.status_code == 422
    assert error_code(response) == "weak_password"
    assert reset(api, link).status_code == 204


def test_verification_link_does_not_reset_password(api):
    api.register()
    assert reset(api, api.last_token()).status_code == 400


def test_reset_link_does_not_verify_address_through_verify_method(api):
    api.verified()
    forgot(api)
    response = api.client.post(f"{AUTH}/verify-email", json={"token": api.last_token()})
    assert response.status_code == 400


def test_reset_confirms_address_and_clears_login_block(api):
    api.register()
    for _ in range(5):
        api.login(password="неверный-пароль-1")
    assert api.login().status_code == 429
    forgot(api)
    assert reset(api, api.last_token()).status_code == 204
    assert api.login(password=NEW_PASSWORD).status_code == 200


def test_recovery_requests_are_limited(api):
    api.verified()
    for _ in range(3):
        assert forgot(api).status_code == 202
    response = forgot(api)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3600"


def test_recovery_from_one_address_is_limited(api):
    for number in range(10):
        assert forgot(api, f"user{number}@example.org").status_code == 202
    assert forgot(api, "another@example.org").status_code == 429


def delete_account(api, token, password=PASSWORD):
    return api.client.post(
        f"{AUTH}/me/delete",
        json={"password": password},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_delete_account_needs_password(api):
    token = api.access_token()
    assert delete_account(api, token, "неверный-пароль-1").status_code == 401
    assert api.me(token).status_code == 200


def test_delete_account_removes_user(api):
    token = api.access_token()
    response = delete_account(api, token)
    assert response.status_code == 204
    assert 'kb_refresh=""' in response.headers["set-cookie"]
    assert api.users.find_by_email(EMAIL) is None
    assert api.me(token).status_code == 401
    assert api.login().status_code == 401


def test_logs_hold_no_passwords_tokens_or_full_addresses(api, caplog):
    with caplog.at_level(logging.INFO, logger="kb"):
        api.verified()
        link = api.last_token()
        api.login()
        api.login(password="неверный-пароль-1")
        forgot(api)
        reset(api, api.last_token())
    text = caplog.text
    assert "a***@example.org" in text
    for secret in (EMAIL, PASSWORD, NEW_PASSWORD, "неверный-пароль-1", link):
        assert secret not in text


def test_access_token_lifetime_comes_from_settings(client_for):
    mailer = MemoryMailer()
    client = client_for(InMemoryRepository(), mailer=mailer, access_token_minutes=5)
    api = Api(client, mailer, None)
    api.verified()
    assert api.login().json()["expires_in"] == 300


def test_token_service_expiry_uses_clock():
    now = [1000.0]
    tokens = TokenService(JWT_SECRET, 60, 600, clock=lambda: now[0])
    token = tokens.issue("u", 1, ACCESS)
    assert tokens.decode(token, ACCESS).user_id == "u"
    now[0] += 59
    assert tokens.decode(token, ACCESS).token_version == 1
    now[0] += 1
    with pytest.raises(InvalidTokenError, match="истёк"):
        tokens.decode(token, ACCESS)
    assert tokens.issue("u", 1, REFRESH) != token


def test_docs_can_be_turned_off():
    app = create_app(
        make_settings(docs_enabled=False),
        repository=InMemoryRepository(),
        cache=MemoryCache(),
        users=InMemoryUserRepository(),
        mailer=MemoryMailer(),
    )
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
