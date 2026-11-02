"""Методы входа и управления учётной записью."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Cookie, Request, Response, status

from kb.api.deps import (
    Auth,
    CurrentUser,
    KnowledgeBaseDep,
    MailerDep,
    SettingsDep,
    client_address,
)
from kb.api.schemas import (
    AcceptedOut,
    DeleteAccountIn,
    ErrorOut,
    ForgotPasswordIn,
    LoginIn,
    PasswordChangeIn,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
    VerifyEmailIn,
)
from kb.auth import AuthService, TokenPair
from kb.config import Settings
from kb.errors import InvalidTokenError

router = APIRouter(prefix="/api/v1/auth", tags=["Аутентификация"])

REFRESH_COOKIE = "kb_refresh"
_COOKIE_PATH = "/api/v1/auth"
# Один ответ для занятого и свободного адреса: иначе чужие адреса можно проверять перебором.
_MAIL_SENT = AcceptedOut(message="Если адрес можно использовать, на него отправлено письмо")

_UNAUTHORIZED: dict[int | str, dict[str, Any]] = {401: {"model": ErrorOut}}
_TOO_MANY: dict[int | str, dict[str, Any]] = {429: {"model": ErrorOut}}
_INVALID: dict[int | str, dict[str, Any]] = {422: {"model": ErrorOut}}


def _issue(response: Response, auth: AuthService, settings: Settings, pair: TokenPair) -> TokenOut:
    # Токен обновления недоступен скрипту страницы и уходит только на методы этого раздела.
    response.set_cookie(
        REFRESH_COOKIE,
        pair.refresh_token,
        max_age=auth.tokens.refresh_seconds,
        path=_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return TokenOut(access_token=pair.access_token, expires_in=auth.tokens.access_seconds)


@router.post(
    "/register",
    response_model=AcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**_INVALID, **_TOO_MANY},
    summary="Зарегистрироваться",
)
def register(
    body: RegisterIn, request: Request, background: BackgroundTasks, auth: Auth, mailer: MailerDep
) -> Any:
    mail = auth.register(body.email, body.password, body.consent, client_address(request))
    # Письмо уходит после ответа: по времени ответа нельзя понять, какое письмо отправлено.
    background.add_task(mailer.send, mail)
    return _MAIL_SENT


@router.post(
    "/verify-email",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={400: {"model": ErrorOut}},
    summary="Подтвердить адрес",
)
def verify_email(body: VerifyEmailIn, auth: Auth) -> None:
    auth.verify_email(body.token)


@router.post(
    "/login",
    response_model=TokenOut,
    responses={**_UNAUTHORIZED, 403: {"model": ErrorOut}, **_TOO_MANY},
    summary="Войти",
)
def login(
    body: LoginIn, request: Request, response: Response, auth: Auth, settings: SettingsDep
) -> Any:
    pair = auth.login(body.email, body.password, client_address(request))
    return _issue(response, auth, settings, pair)


@router.post(
    "/refresh", response_model=TokenOut, responses=_UNAUTHORIZED, summary="Обновить токен доступа"
)
def refresh(
    response: Response,
    auth: Auth,
    settings: SettingsDep,
    kb_refresh: Annotated[str | None, Cookie()] = None,
) -> Any:
    if not kb_refresh:
        raise InvalidTokenError("Нет токена обновления")
    return _issue(response, auth, settings, auth.refresh(kb_refresh))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_UNAUTHORIZED,
    summary="Выйти на всех устройствах",
)
def logout(response: Response, user: CurrentUser, auth: Auth) -> None:
    auth.logout(user)
    response.delete_cookie(REFRESH_COOKIE, path=_COOKIE_PATH)


@router.get("/me", response_model=UserOut, responses=_UNAUTHORIZED, summary="Мои данные")
def me(user: CurrentUser) -> Any:
    return user


@router.post(
    "/password/change",
    response_model=TokenOut,
    responses={**_UNAUTHORIZED, **_INVALID},
    summary="Сменить пароль",
)
def change_password(
    body: PasswordChangeIn,
    response: Response,
    user: CurrentUser,
    auth: Auth,
    settings: SettingsDep,
) -> Any:
    pair = auth.change_password(user, body.current_password, body.new_password)
    return _issue(response, auth, settings, pair)


@router.post(
    "/password/forgot",
    response_model=AcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_TOO_MANY,
    summary="Запросить восстановление пароля",
)
def forgot_password(
    body: ForgotPasswordIn,
    request: Request,
    background: BackgroundTasks,
    auth: Auth,
    mailer: MailerDep,
) -> Any:
    mail = auth.forgot_password(body.email, client_address(request))
    if mail is not None:
        background.add_task(mailer.send, mail)
    return _MAIL_SENT


@router.post(
    "/password/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={400: {"model": ErrorOut}, **_INVALID},
    summary="Задать новый пароль по ссылке из письма",
)
def reset_password(body: ResetPasswordIn, auth: Auth) -> None:
    auth.reset_password(body.token, body.new_password)


@router.post(
    "/me/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_UNAUTHORIZED,
    summary="Удалить учётную запись и все документы",
)
def delete_account(
    body: DeleteAccountIn,
    response: Response,
    user: CurrentUser,
    auth: Auth,
    knowledge_base: KnowledgeBaseDep,
) -> None:
    auth.check_password(user, body.password)
    knowledge_base.delete_user(user.id)
    response.delete_cookie(REFRESH_COOKIE, path=_COOKIE_PATH)
