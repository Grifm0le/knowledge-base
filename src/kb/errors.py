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
