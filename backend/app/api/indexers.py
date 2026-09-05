from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.core.integration_config import IndexerConfig, get_integration_config_store
from app.parser.iso_languages import resolve_language
from app.db.session import get_db
from app.integrations.torznab import TorznabClient
from app.models.auth import AdminUser
from app.models.domain import Indexer
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
from app.services.scheduler import request_health_sweep
from app.services.integration_state import ConfiguredIndexer, get_configured_indexer, list_configured_indexers

def _clean_languages(values: list[str] | None) -> list[str]:
    """Normalise configured languages, dropping anything unrecognised."""
    resolved: list[str] = []
    for value in values or []:
        name = resolve_language(value)
        if name and name not in resolved:
            resolved.append(name)
    return resolved


router = APIRouter(prefix="/indexers", tags=["indexers"])


def get_torznab_client_factory():
    return TorznabClient


def _response(indexer: ConfiguredIndexer) -> IndexerResponse:
    return IndexerResponse(
        id=indexer.id,
        name=indexer.name,
        torznab_url=indexer.torznab_url,
        api_key_configured=bool(indexer.api_key),
        scope=IndexerScope(indexer.scope.value),
        enabled=indexer.enabled,
        timeout_seconds=indexer.timeout_seconds,
        enable_rss=indexer.enable_rss,
        enable_interactive_search=indexer.enable_interactive_search,
        categories=list(indexer.categories),
        minimum_seeders=indexer.minimum_seeders,
        priority=indexer.priority,
        color=indexer.color,
        multi_languages=list(indexer.multi_languages),
        download_client_ids=list(indexer.download_client_ids),
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
    rows = sorted(await list_configured_indexers(db), key=lambda item: item.name.casefold())
    total = len(rows)
    start = (page - 1) * page_size
    rows = rows[start : start + page_size]
    return Collection(
        items=[_response(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


def _require_download_clients(client_ids: list, scope: str) -> None:
    """An indexer with no download client cannot deliver what it finds.

    Enforced here as well as in the UI because the API is reachable directly.
    Skipped when nothing is configured to choose from: requiring a client
    unconditionally would make a fresh install unable to add its first indexer
    until qBittorrent was set up, which is a worse deadlock than the problem.
    """

    clients = {item.id: item for item in get_integration_config_store().list_download_clients()}
    for client_id in client_ids:
        client = clients.get(client_id)
        if client is None:
            raise AppError("DOWNLOAD_CLIENT_NOT_FOUND", "The selected download client no longer exists.", status_code=422)
        if scope != "both" and client.scope != scope:
            raise AppError("DOWNLOAD_CLIENT_SCOPE_MISMATCH", f"{client.name} does not support the indexer's {scope} scope.", status_code=422)
    if client_ids or not clients:
        return
    raise AppError(
        "DOWNLOAD_CLIENT_REQUIRED",
        "Choose at least one download client for this indexer.",
        status_code=422,
    )


@router.post("", response_model=IndexerResponse, status_code=status.HTTP_201_CREATED)
async def create_indexer(
    payload: IndexerCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> IndexerResponse:
    _require_download_clients(payload.download_client_ids, payload.scope.value)
    store = get_integration_config_store()
    config = store.save_indexer(
        IndexerConfig(
            id=uuid4(),
            name=payload.name.strip(),
            torznab_url=str(payload.torznab_url).rstrip("/"),
            api_key=payload.api_key,
            scope=payload.scope.value,
            enabled=payload.enabled,
            timeout_seconds=payload.timeout_seconds,
            enable_rss=payload.enable_rss,
            enable_interactive_search=payload.enable_interactive_search,
            categories=list(payload.categories),
            minimum_seeders=payload.minimum_seeders,
            priority=payload.priority,
            color=payload.color,
            multi_languages=_clean_languages(payload.multi_languages),
            download_client_ids=list(payload.download_client_ids),
        )
    )
    state = Indexer(id=config.id)
    db.add(state)
    await request_health_sweep(db)
    await db.commit()
    indexer = await get_configured_indexer(db, config.id)
    assert indexer is not None
    return _response(indexer)


@router.get("/{indexer_id}", response_model=IndexerResponse)
async def get_indexer(
    indexer_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> IndexerResponse:
    indexer = await get_configured_indexer(db, indexer_id)
    if indexer is None:
        raise AppError("NOT_FOUND", "Indexer was not found.", status_code=404)
    return _response(indexer)


@router.put("/{indexer_id}", response_model=IndexerResponse)
async def update_indexer(
    indexer_id: UUID,
    payload: IndexerUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> IndexerResponse:
    indexer = await get_configured_indexer(db, indexer_id)
    if indexer is None:
        raise AppError("NOT_FOUND", "Indexer was not found.", status_code=404)
    current = indexer.config
    updated = IndexerConfig(
        id=current.id,
        name=(payload.name.strip() if payload.name is not None else current.name),
        torznab_url=(str(payload.torznab_url).rstrip("/") if payload.torznab_url is not None else current.torznab_url),
        api_key=(payload.api_key or current.api_key),
        scope=(payload.scope.value if payload.scope is not None else current.scope),
        enabled=(payload.enabled if payload.enabled is not None else current.enabled),
        timeout_seconds=(payload.timeout_seconds if payload.timeout_seconds is not None else current.timeout_seconds),
        enable_rss=(payload.enable_rss if payload.enable_rss is not None else current.enable_rss),
        enable_interactive_search=(payload.enable_interactive_search if payload.enable_interactive_search is not None else current.enable_interactive_search),
        categories=(list(payload.categories) if payload.categories is not None else list(current.categories)),
        minimum_seeders=(payload.minimum_seeders if payload.minimum_seeders is not None else current.minimum_seeders),
        priority=(payload.priority if payload.priority is not None else current.priority),
        color=((payload.color or None) if payload.color is not None else current.color),
        multi_languages=(_clean_languages(payload.multi_languages) if payload.multi_languages is not None else list(current.multi_languages)),
        download_client_ids=(list(payload.download_client_ids) if payload.download_client_ids is not None else list(current.download_client_ids)),
        revision=current.revision,
    )
    _require_download_clients(updated.download_client_ids, updated.scope)
    try:
        get_integration_config_store().save_indexer(updated, expected_revision=payload.expected_revision)
    except ValueError as exc:
        if str(exc) == "revision_conflict":
            raise AppError("REVISION_CONFLICT", "Indexer changed; refresh and try again.", status_code=409) from exc
        raise
    indexer.health = "unknown"
    indexer.last_error = None
    indexer.latency_ms = None
    await request_health_sweep(db)
    await db.commit()
    refreshed = await get_configured_indexer(db, indexer_id)
    assert refreshed is not None
    return _response(refreshed)


@router.delete("/{indexer_id}", response_model=DeleteResponse)
async def delete_indexer(
    indexer_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    indexer = await get_configured_indexer(db, indexer_id)
    if indexer is None:
        raise AppError("NOT_FOUND", "Indexer was not found.", status_code=404)
    get_integration_config_store().delete_indexer(indexer_id)
    await db.delete(indexer.state)
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
async def test_saved_indexer(
    indexer_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_torznab_client_factory),
) -> IndexerTestResponse:
    indexer = await get_configured_indexer(db, indexer_id)
    if indexer is None:
        raise AppError("NOT_FOUND", "Indexer was not found.", status_code=404)
    result = await refresh_indexer_health(db, indexer, client_factory=client_factory)
    await db.commit()
    return IndexerTestResponse(**result)
