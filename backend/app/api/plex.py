from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.db.session import get_db
from app.integrations.plex import PlexClient
from app.models.auth import AdminUser
from app.models.domain import Movie, Show
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.plex import (
    PlexConfigurationResponse,
    PlexConfigurationUpdate,
    PlexHealthResponse,
    PlexTestRequest,
    PlexTestResponse,
)
from app.services.integration_state import ConfiguredPlex, get_configured_plex
from app.services.jobs import create_job, publish_job_status
from app.services.plex import (
    get_plex_configuration,
    refresh_plex_health,
    test_plex_connection,
)
from app.services.plex_sync import run_plex_library_sync, run_plex_movie_recheck, run_plex_show_recheck
from app.services.runtime_jobs import launch_runtime_job

router = APIRouter(tags=["plex"])


def get_plex_client_factory():
    return PlexClient


def _response(configuration: ConfiguredPlex | None) -> PlexConfigurationResponse:
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


def _health_response(configuration: ConfiguredPlex | None) -> PlexHealthResponse:
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
    current = await get_plex_configuration(db)
    if current is None and not payload.token:
        raise AppError("PLEX_TOKEN_REQUIRED", "A Plex token is required.", status_code=422)
    try:
        config = get_integration_config_store().save_plex(
            id=(current.id if current else None),
            url=str(payload.url).rstrip("/"),
            token=payload.token,
            enabled=payload.enabled,
            expected_revision=payload.expected_revision,
        )
    except ValueError as exc:
        if str(exc) == "revision_conflict":
            raise AppError("REVISION_CONFLICT", "Plex settings changed; refresh and try again.", status_code=409) from exc
        if str(exc) == "secret_required":
            raise AppError("PLEX_TOKEN_REQUIRED", "A Plex token is required.", status_code=422) from exc
        raise
    configuration = await get_configured_plex(db)
    assert configuration is not None and configuration.id == config.id
    configuration.health = "unknown"
    configuration.last_error = None
    configuration.last_checked_at = None
    configuration.latency_ms = None
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


@router.post("/integrations/plex/sync", response_model=JobAcceptedResponse, status_code=202)
async def sync_library(
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedResponse:
    del admin
    configuration = await get_plex_configuration(db)
    if configuration is None or not configuration.enabled:
        raise AppError("PLEX_NOT_CONFIGURED", "Plex is not configured and enabled.", status_code=409)
    job = await create_job(db, "plex_library_sync", summary={"scope": "all"})
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_plex_library_sync(job.id))
    return JobAcceptedResponse(job_id=job.id)


@router.post("/movies/{resource_id}/actions/recheck-plex", response_model=JobAcceptedResponse, status_code=202)
async def recheck_movie(
    resource_id: str,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_plex_client_factory),
) -> JobAcceptedResponse:
    del admin
    configuration = await get_plex_configuration(db)
    if configuration is None or not configuration.enabled:
        raise AppError("PLEX_NOT_CONFIGURED", "Plex is not configured and enabled.", status_code=409)
    statement = select(Movie)
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
    job = await create_job(
        db,
        "plex_movie_recheck",
        summary={
            "movie_id": str(movie.id),
            "movie_title": movie.title,
            "message": f"Checking Plex for {movie.title}…",
        },
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_plex_movie_recheck(job.id, movie.id, client_factory=client_factory))
    return JobAcceptedResponse(job_id=job.id)


@router.post("/shows/{resource_id}/actions/recheck-plex", response_model=JobAcceptedResponse, status_code=202)
async def recheck_show(
    resource_id: str,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_plex_client_factory),
) -> JobAcceptedResponse:
    del admin
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
    job = await create_job(
        db,
        "plex_show_recheck",
        summary={
            "show_id": str(show.id),
            "show_title": show.title,
            "message": f"Checking Plex for {show.title}…",
        },
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_plex_show_recheck(job.id, show.id, client_factory=client_factory))
    return JobAcceptedResponse(job_id=job.id)
