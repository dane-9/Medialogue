import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import blocklist, library, logs, replacements, backups, auth, bulk, custom_formats, discoveries, downloads, duplicates, health, indexers, jobs, movies, plex, problems, quality_profiles, reconciliation, recovery, search, shows, storage, tags, tasks, tmdb, torrent_archive, ui_selections, upgrades
from app.core.config import Settings, ensure_runtime_secret, get_settings, set_settings
from app.core.errors import AppError, app_error_handler, validation_error_handler
from app.core.integration_config import get_integration_config_store
from app.core.logging import configure_logging
from app.db.bootstrap import (
    ensure_default_admin,
    ensure_default_quality_profile,
    refresh_default_profile_scores,
    ensure_builtin_custom_formats,
    production_bootstrap_required,
    ensure_quality_definitions,
    mark_running_jobs_interrupted,
)
from app.db import session as db_session
from app.db.session import configure_database
from app.models.auth import AdminUser
from app.api.dependencies import require_admin
from app.services.integration_state import ensure_configured_integration_states
from app.services.qbittorrent import poll_due_download_clients
from app.services.runtime_jobs import cancel_all_runtime_jobs
from app.services.scheduler import ensure_default_schedules, run_scheduler_loop


async def _qbittorrent_poll_loop(stop_event: asyncio.Event) -> None:
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
    # Resolve the stable production secret before the encrypted integration
    # store is created. Failures here are security-critical and must not be
    # hidden by the non-critical database bootstrap warning below.
    ensure_runtime_secret(settings)
    configure_logging(settings.log_level)
    # /config is the source of truth for integration configuration. Fresh
    # installs intentionally start from the single current schema baseline.
    get_integration_config_store().ensure_initialized()

    if settings.is_production:
        async with db_session.async_session_factory() as db:
            setup_required = await production_bootstrap_required(db)
        if setup_required:
            logger.warning("No administrator yet: open Medialogue in a browser to create one.")

    try:
        async with db_session.async_session_factory() as db:
            if not settings.is_production and settings.bootstrap_admin:
                await ensure_default_admin(db, settings)
            await ensure_quality_definitions(db)
            builtins_changed = await ensure_builtin_custom_formats(db)
            await ensure_default_quality_profile(db)
            # New default scores reach an existing Default profile, for every
            # score the operator has not changed.
            builtins_changed = bool(await refresh_default_profile_scores(db)) or builtins_changed
            if builtins_changed:
                # Built-in conditions and takeover relationships affect saved
                # release score snapshots used by searches and upgrades.
                from app.services.quality_profiles import refresh_all_release_scores
                # Before serving anything, so one transaction blocks nobody.
                await refresh_all_release_scores(db, commit_every=None)
            await mark_running_jobs_interrupted(db)
            await ensure_configured_integration_states(db)
            await ensure_default_schedules(db)
            await db.commit()
    except Exception:
        # Seeding, interrupted-job recovery and default schedules all live
        # here. Serving requests without them means a half-initialized
        # database, so a failure stops startup instead of being a warning.
        logger.critical("database bootstrap failed; refusing to start", exc_info=True)
        raise
    stop_event = asyncio.Event()
    poll_task = asyncio.create_task(_qbittorrent_poll_loop(stop_event))
    scheduler_task = asyncio.create_task(run_scheduler_loop(stop_event))
    from app.services.media_watcher import run_media_watcher
    watcher_task = asyncio.create_task(run_media_watcher(stop_event))
    from app.services.shared_results import run_shared_results_refresher
    results_task = asyncio.create_task(run_shared_results_refresher(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        cancel_all_runtime_jobs()
        # A poll already in flight is waiting on a qBittorrent HTTP call, which
        # can hang for as long as that host feels like. Shutdown must not depend
        # on a remote server answering: give the loop a short grace period, then
        # cancel it. wait_for cancels the task itself on timeout.
        try:
            await asyncio.wait_for(poll_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("qBittorrent poll loop did not stop in time; cancelled")
        try:
            await asyncio.wait_for(scheduler_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("scheduled task loop did not stop in time; cancelled")
        try:
            await asyncio.wait_for(watcher_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("media folder watcher did not stop in time; cancelled")
        try:
            await asyncio.wait_for(results_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("shared results refresher did not stop in time; cancelled")
        from app.integrations.qbittorrent import close_shared_sessions

        await close_shared_sessions()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is not None:
        get_settings.cache_clear()
        set_settings(settings)
        configure_database(settings.database_url)
    app = FastAPI(
        title="Medialogue API",
        version="0.1.0",
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
    app.include_router(storage.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(problems.router, prefix="/api/v1")
    app.include_router(discoveries.router, prefix="/api/v1")
    app.include_router(duplicates.router, prefix="/api/v1")
    app.include_router(movies.router, prefix="/api/v1")
    app.include_router(tags.router, prefix="/api/v1")
    app.include_router(bulk.router, prefix="/api/v1")
    app.include_router(shows.router, prefix="/api/v1")
    app.include_router(plex.router, prefix="/api/v1")
    app.include_router(tmdb.router, prefix="/api/v1")
    app.include_router(downloads.router, prefix="/api/v1")
    app.include_router(blocklist.router, prefix="/api/v1")
    app.include_router(indexers.router, prefix="/api/v1")
    app.include_router(custom_formats.router, prefix="/api/v1")
    app.include_router(quality_profiles.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(reconciliation.router, prefix="/api/v1")
    app.include_router(torrent_archive.router, prefix="/api/v1")
    app.include_router(upgrades.router, prefix="/api/v1")
    app.include_router(recovery.router, prefix="/api/v1")
    app.include_router(backups.router, prefix="/api/v1")
    app.include_router(replacements.router, prefix="/api/v1")
    app.include_router(library.router, prefix="/api/v1")
    app.include_router(ui_selections.router, prefix="/api/v1")
    app.include_router(logs.router, prefix="/api/v1")

    @app.get("/api/openapi.json", include_in_schema=False)
    async def protected_openapi(_: AdminUser = Depends(require_admin)) -> JSONResponse:
        return JSONResponse(app.openapi(), headers={"Cache-Control": "no-store"})

    @app.get("/api/docs", include_in_schema=False)
    async def protected_swagger(_: AdminUser = Depends(require_admin)):
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title=f"{app.title} - Swagger UI")

    @app.get("/api/redoc", include_in_schema=False)
    async def protected_redoc(_: AdminUser = Depends(require_admin)):
        return get_redoc_html(openapi_url="/api/openapi.json", title=f"{app.title} - ReDoc")

    # Library lists run to megabytes of JSON (2,000 movies: 1.3 MB, 83 KB
    # gzipped). Starlette leaves the live event stream uncompressed.
    from starlette.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        # A change made through the API may change what the library and Replace
        # Media list: their shared results must not outlive it.
        if request.method not in {"GET", "HEAD", "OPTIONS"} and response.status_code < 400:
            from app.services.shared_results import invalidate_shared_results
            invalidate_shared_results()
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

        frontend_public = Path(__file__).resolve().parents[2] / "frontend" / "public"
        background = frontend_public / "bg.webp"
        if background.is_file():
            @app.get("/bg.webp", include_in_schema=False)
            async def frontend_background() -> FileResponse:
                return FileResponse(
                    background,
                    media_type="image/webp",
                    headers={"Cache-Control": "no-store"},
                )

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend_fallback(path: str) -> FileResponse:
            requested = (frontend_dist / path).resolve()
            if path and requested.is_file() and frontend_dist.resolve() in requested.parents:
                return FileResponse(requested)
            return FileResponse(frontend_dist / "index.html")
    return app


app = create_app()
