"""Исключения приложения."""


class KnowledgeBaseError(Exception):
    """Базовая ошибка приложения."""


class DocumentValidationError(KnowledgeBaseError):
    """Документ нельзя принять. `code` — машиночитаемая причина для API и журнала."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentNotFoundError(KnowledgeBaseError):
    """Документа с таким идентификатором нет в индексе."""


class UnknownScorerError(KnowledgeBaseError):
    """Запрошен незарегистрированный метод скоринга."""


class InvalidQueryError(KnowledgeBaseError):
    """Некорректные параметры поискового запроса."""
