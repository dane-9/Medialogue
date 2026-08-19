from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.integrations.torznab import TorznabClient
from app.models.auth import AdminUser
from app.models.domain import Indexer, MediaScope
from app.schemas.common import Collection, DeleteResponse
from app.schemas.indexers import (
    IndexerCreate,
    IndexerResponse,
    IndexerScope,
    IndexerTestRequest,
    IndexerTestResponse,
    IndexerUpdate,
)
from app.services.indexers import refresh_indexer_health, test_indexer_connection

router = APIRouter(prefix="/indexers", tags=["indexers"])


def get_torznab_client_factory():
    return TorznabClient


def _response(indexer: Indexer) -> IndexerResponse:
    return IndexerResponse(
        id=indexer.id,
        name=indexer.name,
        torznab_url=indexer.torznab_url,
        api_key_configured=bool(indexer.api_key),
        scope=IndexerScope(indexer.scope.value),
        enabled=indexer.enabled,
        timeout_seconds=indexer.timeout_seconds,
        health=indexer.health,
        last_checked_at=indexer.last_checked_at,
        last_success_at=indexer.last_success_at,
        latency_ms=indexer.latency_ms,
        last_error=indexer.last_error,
        revision=indexer.revision,
        created_at=indexer.created_at,
        updated_at=indexer.updated_at,
    )


@router.get("", response_model=Collection[IndexerResponse])
async def list_indexers(
    page: int = 1,
    page_size: int = 50,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[IndexerResponse]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 250)
    total = int(await db.scalar(select(func.count()).select_from(Indexer)) or 0)
    rows = (
        await db.scalars(
            select(Indexer)
            .order_by(Indexer.name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Collection(
        items=[_response(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


@router.post("", response_model=IndexerResponse, status_code=status.HTTP_201_CREATED)
async def create_indexer(
    payload: IndexerCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> IndexerResponse:
    indexer = Indexer(
        name=payload.name.strip(),
        torznab_url=str(payload.torznab_url).rstrip("/"),
        api_key=payload.api_key,
        scope=MediaScope(payload.scope.value),
        enabled=payload.enabled,
        timeout_seconds=payload.timeout_seconds,
    )
    db.add(indexer)
    await db.commit()
    return _response(indexer)


@router.get("/{indexer_id}", response_model=IndexerResponse)
async def get_indexer(
    indexer_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> IndexerResponse:
    indexer = await db.get(Indexer, indexer_id)
    if indexer is None:
        raise AppError("NOT_FOUND", "Indexer was not found.", status_code=404)
    return _response(indexer)


@router.put("/{indexer_id}", response_model=IndexerResponse)
@router.patch("/{indexer_id}", response_model=IndexerResponse, include_in_schema=False)
async def update_indexer(
    indexer_id: UUID,
    payload: IndexerUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> IndexerResponse:
    indexer = await db.get(Indexer, indexer_id)
    if indexer is None:
        raise AppError("NOT_FOUND", "Indexer was not found.", status_code=404)
    if payload.expected_revision is not None and payload.expected_revision != indexer.revision:
        raise AppError("REVISION_CONFLICT", "Indexer changed; refresh and try again.", status_code=409)
    values = payload.model_dump(exclude_unset=True)
    values.pop("expected_revision", None)
    api_key = values.pop("api_key", None)
    if api_key:
        indexer.api_key = api_key
    if "torznab_url" in values and values["torznab_url"] is not None:
        values["torznab_url"] = str(values["torznab_url"]).rstrip("/")
    if "scope" in values and values["scope"] is not None:
        scope = values["scope"]
        values["scope"] = MediaScope(scope.value if hasattr(scope, "value") else str(scope))
    for key, value in values.items():
        setattr(indexer, key, value)
    indexer.revision += 1
    indexer.health = "unknown"
    indexer.last_error = None
    indexer.latency_ms = None
    await db.commit()
    return _response(indexer)


@router.delete("/{indexer_id}", response_model=DeleteResponse)
async def delete_indexer(
    indexer_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    indexer = await db.get(Indexer, indexer_id)
    if indexer is None:
        raise AppError("NOT_FOUND", "Indexer was not found.", status_code=404)
    await db.delete(indexer)
    await db.commit()
    return DeleteResponse(id=indexer_id)


@router.post("/test", response_model=IndexerTestResponse)
async def test_unsaved_indexer(
    payload: IndexerTestRequest,
    _: object = Depends(require_csrf),
    client_factory=Depends(get_torznab_client_factory),
) -> IndexerTestResponse:
    try:
        result = await test_indexer_connection(
            url=str(payload.torznab_url).rstrip("/"),
            api_key=payload.api_key,
            timeout_seconds=payload.timeout_seconds,
            client_factory=client_factory,
        )
        return IndexerTestResponse(**result)
    except Exception as exc:
        return IndexerTestResponse(status="unavailable", message=str(exc))


@router.post("/{indexer_id}/test", response_model=IndexerTestResponse)
@router.post("/{indexer_id}/health/refresh", response_model=IndexerTestResponse, include_in_schema=False)
async def test_saved_indexer(
    indexer_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_torznab_client_factory),
) -> IndexerTestResponse:
    indexer = await db.get(Indexer, indexer_id)
    if indexer is None:
        raise AppError("NOT_FOUND", "Indexer was not found.", status_code=404)
    result = await refresh_indexer_health(db, indexer, client_factory=client_factory)
    await db.commit()
    return IndexerTestResponse(**result)
