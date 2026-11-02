"""Методы REST API. Здесь только разбор запроса и формирование ответа, логика в сервисе."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status

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
    SearchResponse,
    StatsOut,
    VersionOut,
    VersionSummary,
)
from kb.search import MAX_LIMIT, SearchFilters, SearchQuery
from kb.service import KnowledgeBase, search_methods

router = APIRouter(prefix="/api/v1")

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorOut}}
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0)]


def get_knowledge_base(request: Request) -> KnowledgeBase:
    knowledge_base: KnowledgeBase = request.app.state.knowledge_base
    return knowledge_base


Service = Annotated[KnowledgeBase, Depends(get_knowledge_base)]


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
    responses={**NOT_FOUND, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    tags=["Документы"],
    summary="Сохранить новую версию",
)
def update_document(doc_id: UUID, body: DocumentUpdate, service: Service) -> Any:
    return service.update_document(str(doc_id), body.content, body.expected_version)


@router.delete(
    "/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
    tags=["Документы"],
    summary="Удалить документ",
)
def delete_document(doc_id: UUID, service: Service) -> None:
    service.delete_document(str(doc_id))


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
    responses=NOT_FOUND,
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
    )
    outcome = service.search(query)
    response.headers["X-Cache"] = "HIT" if outcome.cache_hit else "MISS"
    return outcome.payload


@router.get("/stats", response_model=StatsOut, tags=["Служебные"], summary="Статистика")
def stats(service: Service) -> Any:
    return service.stats()


@router.get(
    "/health",
    response_model=HealthOut,
    responses={503: {"model": ErrorOut}},
    tags=["Служебные"],
    summary="Проверка состояния",
)
def health(service: Service) -> Any:
    service.check_storage()
    cache_ok = service.check_cache()
    return HealthOut(
        status="ok" if cache_ok else "degraded",
        database="ok",
        cache="ok" if cache_ok else "unavailable",
    )
