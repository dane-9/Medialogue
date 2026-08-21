import logging
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api import auth, bulk, custom_formats, downloads, duplicates, health, indexers, jobs, movies, operations, parser, plex, problems, quality_profiles, reconciliation, recovery, search, setup, shows, storage, tags, tmdb, torrent_archive
from app.core.config import Settings, get_settings, set_settings
from app.core.errors import AppError, app_error_handler, validation_error_handler
from app.core.integration_config import get_integration_config_store
from app.core.logging import configure_logging
from app.db.bootstrap import (
    ensure_default_admin,
    ensure_problem_integrity,
    ensure_quality_definitions,
    mark_running_jobs_interrupted,
)
from app.db import session as db_session
from app.db.session import configure_database
from app.models.auth import AdminUser
from app.api.dependencies import require_admin
from app.services.integration_state import ensure_configured_integration_states
from app.services.qbittorrent import poll_due_download_clients
from app.services.recovery import cleanup_expired_recovery_exports
from app.services.runtime_jobs import cancel_all_runtime_jobs


async def _qbit_poll_loop(stop_event: asyncio.Event) -> None:
    """Cancellable, non-overlapping qBit observer loop.

    qBittorrent observations run whenever configured.  Destructive filesystem
    actions remain protected by their explicit preview/confirmation workflows.
    """

    running = False
    while not stop_event.is_set():
        if not running:
            running = True
            try:
                async with db_session.async_session_factory() as db:
                    await poll_due_download_clients(db)
            except Exception:
                logger.warning("qBittorrent polling cycle failed", exc_info=True)
            finally:
                running = False
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    # /config is the source of truth for integration configuration. Fresh
    # installs intentionally start empty; there is no legacy DB import path.
    get_integration_config_store().ensure_initialized()
    try:
        async with db_session.async_session_factory() as db:
            if settings.bootstrap_admin:
                await ensure_default_admin(db, settings)
            await ensure_quality_definitions(db)
            await mark_running_jobs_interrupted(db)
            await ensure_problem_integrity(db)
            await ensure_configured_integration_states(db)
            await db.commit()
    except Exception:
        # Migrations may be run after the process is started in local
        # development. Liveness remains useful while the DB is offline;
        # readiness reports the actual database state.
        logger.warning("database bootstrap skipped", exc_info=True)
    cleanup_expired_recovery_exports(settings)
    stop_event = asyncio.Event()
    poll_task = asyncio.create_task(_qbit_poll_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        cancel_all_runtime_jobs()
        await poll_task


def create_app(settings: Settings | None = None) -> FastAPI:
    operations.reset_active_operations()
    if settings is not None:
        get_settings.cache_clear()
        set_settings(settings)
        configure_database(settings.database_url)
    app = FastAPI(
        title="Medialogue API",
        version="0.1.0",
        # API documentation is useful for the administrator, but it should not
        # disclose the complete internal API surface to unauthenticated LAN
        # clients. Protected routes are registered below.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_exception_handler(AppError, app_error_handler)
    from fastapi.exceptions import RequestValidationError

    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(setup.router, prefix="/api/v1")
    app.include_router(storage.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(problems.router, prefix="/api/v1")
    app.include_router(duplicates.router, prefix="/api/v1")
    app.include_router(parser.router, prefix="/api/v1")
    app.include_router(operations.router, prefix="/api/v1")
    app.include_router(movies.router, prefix="/api/v1")
    app.include_router(tags.router, prefix="/api/v1")
    app.include_router(bulk.router, prefix="/api/v1")
    app.include_router(shows.router, prefix="/api/v1")
    app.include_router(plex.router, prefix="/api/v1")
    app.include_router(tmdb.router, prefix="/api/v1")
    app.include_router(downloads.router, prefix="/api/v1")
    app.include_router(indexers.router, prefix="/api/v1")
    app.include_router(custom_formats.router, prefix="/api/v1")
    app.include_router(quality_profiles.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(reconciliation.router, prefix="/api/v1")
    app.include_router(torrent_archive.router, prefix="/api/v1")
    app.include_router(recovery.router, prefix="/api/v1")

    @app.get("/api/openapi.json", include_in_schema=False)
    async def protected_openapi(_: AdminUser = Depends(require_admin)) -> JSONResponse:
        return JSONResponse(app.openapi(), headers={"Cache-Control": "no-store"})

    @app.get("/api/docs", include_in_schema=False)
    async def protected_swagger(_: AdminUser = Depends(require_admin)):
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title=f"{app.title} - Swagger UI")

    @app.get("/api/redoc", include_in_schema=False)
    async def protected_redoc(_: AdminUser = Depends(require_admin)):
        return get_redoc_html(openapi_url="/api/openapi.json", title=f"{app.title} - ReDoc")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    frontend_dist = Path("/app/frontend/dist")
    if not frontend_dist.is_dir():
        frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        assets = frontend_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend_fallback(path: str) -> FileResponse:
            # API and health paths are matched by the explicit routes above.
            # Unknown browser routes receive index.html for React Router.
            requested = (frontend_dist / path).resolve()
            if path and requested.is_file() and frontend_dist.resolve() in requested.parents:
                return FileResponse(requested)
            return FileResponse(frontend_dist / "index.html")
    return app


app = create_app()
