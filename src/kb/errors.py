"""Исключения приложения."""


class KnowledgeBaseError(Exception):
    """Базовая ошибка приложения. `code` — машиночитаемая причина для API и журнала."""

    code = "internal_error"


class DocumentValidationError(KnowledgeBaseError):
    """Документ нельзя принять."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentNotFoundError(KnowledgeBaseError):
    """Документа с таким идентификатором нет."""

    code = "document_not_found"


class VersionNotFoundError(KnowledgeBaseError):
    """У документа нет такой версии."""

    code = "version_not_found"


class VersionConflictError(KnowledgeBaseError):
    """Документ изменён другим клиентом после того, как его прочитал этот."""

    code = "version_conflict"


class DuplicateSourceError(KnowledgeBaseError):
    """Документ из файла с таким именем уже есть."""

    code = "duplicate_source"


class UnknownScorerError(KnowledgeBaseError):
    """Запрошен незарегистрированный метод скоринга."""

    code = "unknown_scorer"


class InvalidQueryError(KnowledgeBaseError):
    """Некорректные параметры поискового запроса."""

    code = "invalid_query"


class AuthenticationError(KnowledgeBaseError):
    """Неверный адрес или пароль. Сообщение не говорит, что именно не подошло."""

    code = "invalid_credentials"


class InvalidTokenError(KnowledgeBaseError):
    """Токен доступа отсутствует, подделан, просрочен или отозван."""

    code = "invalid_token"


class OneTimeTokenError(KnowledgeBaseError):
    """Разовый токен из письма не подходит: неверный, просроченный или уже использован."""

    code = "invalid_one_time_token"


class EmailNotVerifiedError(KnowledgeBaseError):
    """Адрес не подтверждён. Сообщается только после проверки пароля."""

    code = "email_not_verified"


class WeakPasswordError(KnowledgeBaseError):
    """Пароль не соответствует правилам."""

    code = "weak_password"


class ConsentRequiredError(KnowledgeBaseError):
    """Нет согласия на обработку адреса электронной почты."""

    code = "consent_required"


class AccessDeniedError(KnowledgeBaseError):
    """Документ виден пользователю, но такое действие ему не разрешено."""

    code = "access_denied"


class UserNotFoundError(KnowledgeBaseError):
    """Пользователя с таким адресом нет."""

    code = "user_not_found"


class TooManyRequestsError(KnowledgeBaseError):
    """Превышено число попыток."""

    code = "too_many_requests"

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after
