from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.api.operations import active_operations_enabled
from app.core.errors import AppError
from app.db.session import get_db
from app.integrations.plex import PlexClient
from app.models.auth import AdminUser
from app.models.domain import MediaDirectory, Movie, MovieRelease, PlexConfiguration, Show
from app.schemas.plex import (
    PlexConfigurationResponse,
    PlexConfigurationUpdate,
    PlexHealthResponse,
    PlexRecheckResponse,
    PlexTestRequest,
    PlexTestResponse,
)
from app.services.plex import (
    get_plex_configuration,
    recheck_movie_plex,
    recheck_show_plex,
    refresh_plex_health,
    test_plex_connection,
)

router = APIRouter(tags=["plex"])


def get_plex_client_factory():
    return PlexClient


def _response(configuration: PlexConfiguration | None) -> PlexConfigurationResponse:
    if configuration is None:
        return PlexConfigurationResponse(configured=False)
    return PlexConfigurationResponse(
        configured=True,
        url=configuration.url,
        token_configured=bool(configuration.token),
        enabled=configuration.enabled,
        health=configuration.health,
        machine_identifier=configuration.machine_identifier,
        last_checked_at=configuration.last_checked_at,
        last_success_at=configuration.last_success_at,
        latency_ms=configuration.latency_ms,
        last_error=configuration.last_error,
        revision=configuration.revision,
    )


def _health_response(configuration: PlexConfiguration | None) -> PlexHealthResponse:
    if configuration is None:
        return PlexHealthResponse()
    return PlexHealthResponse(
        configured=True,
        enabled=configuration.enabled,
        status=configuration.health if configuration.enabled else "unknown",
        machine_identifier=configuration.machine_identifier,
        last_success=configuration.last_success_at,
        latency_ms=configuration.latency_ms,
        last_error=configuration.last_error,
    )


@router.get("/integrations/plex", response_model=PlexConfigurationResponse)
async def get_configuration(
    _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> PlexConfigurationResponse:
    return _response(await get_plex_configuration(db))


@router.put("/integrations/plex", response_model=PlexConfigurationResponse)
async def save_configuration(
    payload: PlexConfigurationUpdate,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PlexConfigurationResponse:
    del admin
    configuration = await get_plex_configuration(db)
    if configuration is None:
        if not payload.token:
            raise AppError("PLEX_TOKEN_REQUIRED", "A Plex token is required.", status_code=422)
        configuration = PlexConfiguration(
            url=str(payload.url).rstrip("/"), token=payload.token, enabled=payload.enabled
        )
        db.add(configuration)
    else:
        if payload.expected_revision is not None and payload.expected_revision != configuration.revision:
            raise AppError("REVISION_CONFLICT", "Plex settings changed; refresh and try again.", status_code=409)
        configuration.url = str(payload.url).rstrip("/")
        if payload.token:
            configuration.token = payload.token
        configuration.enabled = payload.enabled
        configuration.health = "unknown"
        configuration.last_error = None
        configuration.revision += 1
    await db.commit()
    return _response(configuration)


@router.post("/integrations/plex/test", response_model=PlexTestResponse)
async def test_connection(
    payload: PlexTestRequest,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_plex_client_factory),
) -> PlexTestResponse:
    del admin
    configuration = await get_plex_configuration(db)
    url = str(payload.url).rstrip("/") if payload.url else (configuration.url if configuration else None)
    token = payload.token or (configuration.token if configuration else None)
    if not url or not token:
        raise AppError("PLEX_CONFIGURATION_REQUIRED", "Provide a Plex URL and token.", status_code=422)
    try:
        result = await test_plex_connection(url, token, client_factory=client_factory)
    except Exception as exc:
        return PlexTestResponse(status="unavailable", message=str(exc))
    return PlexTestResponse(**result)


@router.get("/integrations/plex/health", response_model=PlexHealthResponse)
async def plex_health(
    _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> PlexHealthResponse:
    return _health_response(await get_plex_configuration(db))


@router.post("/integrations/plex/health/refresh", response_model=PlexTestResponse)
async def refresh_health(
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_plex_client_factory),
) -> PlexTestResponse:
    del admin
    configuration = await get_plex_configuration(db)
    if configuration is None or not configuration.enabled:
        raise AppError("PLEX_NOT_CONFIGURED", "Plex is not configured and enabled.", status_code=409)
    result = await refresh_plex_health(db, configuration, client_factory=client_factory)
    await db.commit()
    return PlexTestResponse(**result)


@router.post("/movies/{resource_id}/actions/recheck-plex", response_model=PlexRecheckResponse, response_model_exclude_none=True)
async def recheck_movie(
    resource_id: str,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_plex_client_factory),
) -> PlexRecheckResponse:
    del admin
    if not active_operations_enabled():
        raise AppError(
            "ACTIVE_OPERATIONS_LOCKED",
            "Enable Active Operations before rechecking Plex.",
            status_code=423,
        )
    configuration = await get_plex_configuration(db)
    if configuration is None or not configuration.enabled:
        raise AppError("PLEX_NOT_CONFIGURED", "Plex is not configured and enabled.", status_code=409)
    statement = select(Movie).options(
        selectinload(Movie.releases)
        .selectinload(MovieRelease.directories)
        .selectinload(MediaDirectory.files)
    )
    if resource_id.isdigit():
        statement = statement.where(Movie.tmdb_id == int(resource_id))
    else:
        try:
            movie_id = UUID(resource_id)
        except ValueError as exc:
            raise AppError("NOT_FOUND", "Movie was not found.", status_code=404) from exc
        statement = statement.where(Movie.id == movie_id)
    movie = (await db.scalars(statement)).unique().one_or_none()
    if movie is None:
        raise AppError("NOT_FOUND", "Movie was not found.", status_code=404)
    result = await recheck_movie_plex(db, movie, configuration, client_factory=client_factory)
    await db.commit()
    return PlexRecheckResponse(movie_id=str(movie.id), **result)


@router.post("/shows/{resource_id}/actions/recheck-plex", response_model=PlexRecheckResponse, response_model_exclude_none=True)
async def recheck_show(
    resource_id: str,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_plex_client_factory),
) -> PlexRecheckResponse:
    del admin
    if not active_operations_enabled():
        raise AppError(
            "ACTIVE_OPERATIONS_LOCKED",
            "Enable Active Operations before rechecking Plex.",
            status_code=423,
        )
    configuration = await get_plex_configuration(db)
    if configuration is None or not configuration.enabled:
        raise AppError("PLEX_NOT_CONFIGURED", "Plex is not configured and enabled.", status_code=409)
    if resource_id.isdigit():
        show = await db.scalar(select(Show).where(Show.tmdb_id == int(resource_id)))
    else:
        try:
            show_id = UUID(resource_id)
        except ValueError as exc:
            raise AppError("NOT_FOUND", "Show was not found.", status_code=404) from exc
        show = await db.get(Show, show_id)
    if show is None:
        raise AppError("NOT_FOUND", "Show was not found.", status_code=404)
    result = await recheck_show_plex(db, show, configuration, client_factory=client_factory)
    await db.commit()
    return PlexRecheckResponse(show_id=str(show.id), **result)
