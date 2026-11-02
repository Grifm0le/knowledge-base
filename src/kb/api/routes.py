"""Методы REST API. Здесь только разбор запроса и формирование ответа, логика в сервисе."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from kb.api.deps import KnowledgeBaseDep, Service, get_workspace
from kb.api.schemas import (
    DiffOut,
    DocumentCreate,
    DocumentOut,
    DocumentPage,
    DocumentSummary,
    DocumentUpdate,
    ErrorOut,
    HealthOut,
    ImportOut,
    PreviewOut,
    SearchResponse,
    ShareIn,
    ShareOut,
    StatsOut,
    VersionOut,
    VersionSummary,
)
from kb.scoring import DEFAULT_FIELD_WEIGHTS
from kb.search import MAX_LIMIT, SearchFilters, SearchQuery
from kb.service import search_methods

# Все методы работы с данными требуют токен доступа: без него ответ 401.
router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(get_workspace)],
    responses={401: {"model": ErrorOut}},
)
public_router = APIRouter(prefix="/api/v1")

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorOut}}
FORBIDDEN: dict[int | str, dict[str, Any]] = {403: {"model": ErrorOut}}
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0)]
Weight = Annotated[float | None, Query(ge=0, le=100)]

DEFAULT_METHOD_WEIGHTS = {"bm25": 0.7, "tfidf": 0.3}


def _custom_weights(
    defaults: Mapping[str, float], given: dict[str, float | None]
) -> tuple[tuple[str, float], ...]:
    """Заданные веса поверх значений по умолчанию. Порядок постоянный: это часть ключа кэша."""
    changed = {name: value for name, value in given.items() if value is not None}
    if not changed:
        return ()
    return tuple(sorted({**defaults, **changed}.items()))


@router.post(
    "/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": ErrorOut}},
    tags=["Документы"],
    summary="Создать документ",
)
def create_document(body: DocumentCreate, service: Service) -> Any:
    return service.create_document(body.content, body.source_name)


@router.post(
    "/documents/import", response_model=ImportOut, tags=["Документы"], summary="Импорт файлов"
)
def import_documents(files: Annotated[list[UploadFile], File()], service: Service) -> Any:
    return service.import_files((file.filename or "", file.file.read()) for file in files)


@router.get(
    "/documents", response_model=DocumentPage, tags=["Документы"], summary="Список документов"
)
def list_documents(service: Service, limit: Limit = 20, offset: Offset = 0) -> Any:
    items, total = service.list_documents(limit, offset)
    return DocumentPage(
        items=[DocumentSummary.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/documents/{doc_id}",
    response_model=DocumentOut,
    responses=NOT_FOUND,
    tags=["Документы"],
    summary="Получить документ",
)
def get_document(doc_id: UUID, service: Service) -> Any:
    return service.get_document(str(doc_id))


@router.put(
    "/documents/{doc_id}",
    response_model=DocumentOut,
    responses={**NOT_FOUND, **FORBIDDEN, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    tags=["Документы"],
    summary="Сохранить новую версию",
)
def update_document(doc_id: UUID, body: DocumentUpdate, service: Service) -> Any:
    return service.update_document(str(doc_id), body.content, body.expected_version)


@router.delete(
    "/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, **FORBIDDEN},
    tags=["Документы"],
    summary="Удалить документ",
)
def delete_document(doc_id: UUID, service: Service) -> None:
    service.delete_document(str(doc_id))


@router.get(
    "/documents/{doc_id}/preview",
    response_model=PreviewOut,
    responses=NOT_FOUND,
    tags=["Документы"],
    summary="Документ в виде HTML",
)
def preview_document(doc_id: UUID, service: Service) -> Any:
    return PreviewOut(html=service.preview(str(doc_id)))


@router.get(
    "/documents/{doc_id}/shares",
    response_model=list[ShareOut],
    responses={**NOT_FOUND, **FORBIDDEN},
    tags=["Доступ"],
    summary="Кому открыт документ",
)
def list_shares(doc_id: UUID, service: Service) -> Any:
    return service.list_shares(str(doc_id))


@router.put(
    "/documents/{doc_id}/shares",
    response_model=ShareOut,
    responses={**NOT_FOUND, **FORBIDDEN, 429: {"model": ErrorOut}},
    tags=["Доступ"],
    summary="Открыть документ пользователю",
)
def share_document(doc_id: UUID, body: ShareIn, service: Service) -> Any:
    return service.share(str(doc_id), body.email, body.permission)


@router.delete(
    "/documents/{doc_id}/shares/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, **FORBIDDEN},
    tags=["Доступ"],
    summary="Отозвать доступ",
)
def unshare_document(doc_id: UUID, user_id: UUID, service: Service) -> None:
    service.unshare(str(doc_id), str(user_id))


@router.get(
    "/documents/{doc_id}/versions",
    response_model=list[VersionSummary],
    responses=NOT_FOUND,
    tags=["Версии"],
    summary="История версий",
)
def list_versions(doc_id: UUID, service: Service) -> Any:
    return service.list_versions(str(doc_id))


@router.get(
    "/documents/{doc_id}/versions/{version}",
    response_model=VersionOut,
    responses=NOT_FOUND,
    tags=["Версии"],
    summary="Получить версию",
)
def get_version(doc_id: UUID, version: int, service: Service) -> Any:
    return service.get_version(str(doc_id), version)


@router.post(
    "/documents/{doc_id}/versions/{version}/restore",
    response_model=DocumentOut,
    responses={**NOT_FOUND, **FORBIDDEN},
    tags=["Версии"],
    summary="Вернуть старую версию",
)
def restore_version(doc_id: UUID, version: int, service: Service) -> Any:
    return service.restore_version(str(doc_id), version)


@router.get(
    "/documents/{doc_id}/diff",
    response_model=DiffOut,
    responses=NOT_FOUND,
    tags=["Версии"],
    summary="Сравнить две версии",
)
def diff_versions(doc_id: UUID, base: int, target: int, service: Service) -> Any:
    diff = service.diff_versions(str(doc_id), base, target)
    return DiffOut(base=base, target=target, diff=diff)


@router.get(
    "/search",
    response_model=SearchResponse,
    responses={400: {"model": ErrorOut}},
    tags=["Поиск"],
    summary="Полнотекстовый поиск",
    description=f"Методы ранжирования: {', '.join(search_methods())}.",
)
def search(
    response: Response,
    service: Service,
    q: str = "",
    scorer: str = "bm25",
    tags: Annotated[list[str] | None, Query()] = None,
    category: str | None = None,
    author: str | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    sort: str = "relevance",
    limit: Limit = 10,
    offset: Offset = 0,
    w_title: Weight = None,
    w_headings: Weight = None,
    w_tags: Weight = None,
    w_body: Weight = None,
    w_code: Weight = None,
    hybrid_bm25: Weight = None,
    hybrid_tfidf: Weight = None,
) -> Any:
    query = SearchQuery(
        text=q,
        filters=SearchFilters(
            tags=tuple(tag.lower() for tag in tags or ()),
            category=category,
            author=author,
            created_from=created_from,
            created_to=created_to,
        ),
        scorer=scorer,
        sort=sort,
        limit=limit,
        offset=offset,
        field_weights=_custom_weights(
            DEFAULT_FIELD_WEIGHTS,
            {
                "title": w_title,
                "headings": w_headings,
                "tags": w_tags,
                "body": w_body,
                "code": w_code,
            },
        ),
        method_weights=_custom_weights(
            DEFAULT_METHOD_WEIGHTS, {"bm25": hybrid_bm25, "tfidf": hybrid_tfidf}
        ),
    )
    outcome = service.search(query)
    response.headers["X-Cache"] = "HIT" if outcome.cache_hit else "MISS"
    return outcome.payload


@router.get("/stats", response_model=StatsOut, tags=["Служебные"], summary="Статистика")
def stats(service: Service) -> Any:
    return service.stats()


@public_router.get(
    "/health",
    response_model=HealthOut,
    responses={503: {"model": ErrorOut}},
    tags=["Служебные"],
    summary="Проверка состояния",
)
def health(knowledge_base: KnowledgeBaseDep) -> Any:
    knowledge_base.check_storage()
    cache_ok = knowledge_base.check_cache()
    return HealthOut(
        status="ok" if cache_ok else "degraded",
        database="ok",
        cache="ok" if cache_ok else "unavailable",
    )
