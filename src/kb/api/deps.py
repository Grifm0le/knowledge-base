"""Зависимости методов API: сервисы из состояния приложения и текущий пользователь."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kb.auth import AuthService
from kb.config import Settings
from kb.errors import InvalidTokenError
from kb.mailer import Mailer
from kb.service import KnowledgeBase, Workspace
from kb.storage.users import User

_bearer = HTTPBearer(auto_error=False, description="Токен из POST /api/v1/auth/login")


def get_knowledge_base(request: Request) -> KnowledgeBase:
    knowledge_base: KnowledgeBase = request.app.state.knowledge_base
    return knowledge_base


def get_auth(request: Request) -> AuthService:
    auth: AuthService = request.app.state.auth
    return auth


def get_mailer(request: Request) -> Mailer:
    mailer: Mailer = request.app.state.mailer
    return mailer


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


KnowledgeBaseDep = Annotated[KnowledgeBase, Depends(get_knowledge_base)]
Auth = Annotated[AuthService, Depends(get_auth)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)], auth: Auth
) -> User:
    if credentials is None:
        raise InvalidTokenError("Нужен токен доступа")
    return auth.authenticate(credentials.credentials)


CurrentUser = Annotated[User, Depends(current_user)]


def get_workspace(user: CurrentUser, knowledge_base: KnowledgeBaseDep) -> Workspace:
    return knowledge_base.workspace(user.id)


Service = Annotated[Workspace, Depends(get_workspace)]


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"
