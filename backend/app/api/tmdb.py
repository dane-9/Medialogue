from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.db.session import get_db
from app.integrations.tmdb import TMDBClient
from app.models.auth import AdminUser
from app.schemas.tmdb import TMDBConfigurationResponse, TMDBConfigurationUpdate
from app.services.integration_state import ConfiguredTMDB, get_configured_tmdb
from app.services.tmdb import get_tmdb_configuration
from app.services.scheduler import request_health_sweep

router = APIRouter(tags=["tmdb"])


def get_tmdb_client_factory():
    return TMDBClient


def _response(configuration: ConfiguredTMDB | None) -> TMDBConfigurationResponse:
    if configuration is None:
        return TMDBConfigurationResponse(configured=False)
    return TMDBConfigurationResponse(
        configured=True,
        api_key_configured=bool(configuration.api_key),
        enabled=configuration.enabled,
        health=configuration.health,
        last_checked_at=configuration.last_checked_at,
        last_success_at=configuration.last_success_at,
        latency_ms=configuration.latency_ms,
        last_error=configuration.last_error,
        revision=configuration.revision,
    )


@router.get("/integrations/tmdb", response_model=TMDBConfigurationResponse)
async def get_configuration(
    _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> TMDBConfigurationResponse:
    return _response(await get_tmdb_configuration(db))


@router.put("/integrations/tmdb", response_model=TMDBConfigurationResponse)
async def save_configuration(
    payload: TMDBConfigurationUpdate,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TMDBConfigurationResponse:
    del admin
    current = await get_tmdb_configuration(db)
    if current is None and not payload.api_key:
        raise AppError("TMDB_API_KEY_REQUIRED", "A TMDB API key is required.", status_code=422)
    try:
        config = get_integration_config_store().save_tmdb(
            id=(current.id if current else None),
            api_key=payload.api_key,
            enabled=payload.enabled,
            expected_revision=payload.expected_revision,
        )
    except ValueError as exc:
        if str(exc) == "revision_conflict":
            raise AppError("REVISION_CONFLICT", "TMDB settings changed; refresh and try again.", status_code=409) from exc
        if str(exc) == "secret_required":
            raise AppError("TMDB_API_KEY_REQUIRED", "A TMDB API key is required.", status_code=422) from exc
        raise
    configuration = await get_configured_tmdb(db)
    assert configuration is not None and configuration.id == config.id
    configuration.health = "unknown"
    configuration.last_error = None
    configuration.last_checked_at = None
    configuration.latency_ms = None
    await request_health_sweep(db)
    await db.commit()
    return _response(configuration)
