from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.core.config import get_settings
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import StorageRoot, Torrent, TorrentArchiveState

from app.services.integration_state import get_configured_plex, get_configured_tmdb, list_configured_download_clients, list_configured_indexers
from app.services.torrent_archive import archive_mount_health

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/v1/health")
async def api_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readiness(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return {"status": "not_ready", "database": "unavailable"}
    return {"status": "ok", "database": "available"}


@router.get("/integrations/health")
@router.get("/api/v1/integrations/health")
async def integration_health(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    roots = (await db.scalars(select(StorageRoot))).all()
    plex = await get_configured_plex(db)
    tmdb = await get_configured_tmdb(db)
    clients = sorted(await list_configured_download_clients(db), key=lambda item: item.name.casefold())
    indexers = sorted(await list_configured_indexers(db), key=lambda item: item.name.casefold())
    archive_health = archive_mount_health()
    archive_total = int(await db.scalar(select(func.count()).select_from(Torrent)) or 0)
    archive_complete = int(
        await db.scalar(
            select(func.count()).select_from(Torrent).where(Torrent.archive_state == TorrentArchiveState.ARCHIVED)
        )
        or 0
    )
    enabled_clients = [client for client in clients if client.enabled]
    healthy_clients = [client for client in enabled_clients if client.health == "healthy"]
    if not enabled_clients:
        qbit_status = "unknown"
    elif len(healthy_clients) == len(enabled_clients):
        qbit_status = "healthy"
    elif healthy_clients:
        qbit_status = "degraded"
    elif any(client.health == "unavailable" for client in enabled_clients):
        qbit_status = "unavailable"
    else:
        qbit_status = "unknown"
    enabled_indexers = [indexer for indexer in indexers if indexer.enabled]
    healthy_indexers = [indexer for indexer in enabled_indexers if indexer.health == "healthy"]
    if not enabled_indexers:
        indexer_status = "unknown"
    elif len(healthy_indexers) == len(enabled_indexers):
        indexer_status = "healthy"
    elif healthy_indexers:
        indexer_status = "degraded"
    elif any(indexer.health == "unavailable" for indexer in enabled_indexers):
        indexer_status = "unavailable"
    else:
        indexer_status = "unknown"
    return {
        "database": {"status": "healthy"},
        "plex": {
            "configured": plex is not None,
            "status": plex.health if plex and plex.enabled else "unknown",
            "last_success": plex.last_success_at if plex else None,
            "latency_ms": plex.latency_ms if plex else None,
        },
        "tmdb": {
            "configured": tmdb is not None,
            "status": tmdb.health if tmdb and tmdb.enabled else "unknown",
            "last_success": tmdb.last_success_at if tmdb else None,
            "latency_ms": tmdb.latency_ms if tmdb else None,
        },
        "qbittorrent": {
            "configured": bool(clients),
            "status": qbit_status,
            "healthy_clients": len(healthy_clients),
            "total_clients": len(enabled_clients),
            "clients": [
                {
                    "id": str(client.id),
                    "name": client.name,
                    "scope": client.scope.value,
                    "enabled": client.enabled,
                    "health": client.health or "unknown",
                    "last_polled_at": client.last_polled_at,
                    "last_health_checked_at": client.last_health_checked_at,
                    "last_success_at": client.last_success_at,
                    "latency_ms": client.latency_ms,
                    "last_error": client.last_error,
                }
                for client in clients
            ],
        },
        "indexers": {
            "configured": bool(indexers),
            "enabled": len(enabled_indexers),
            "healthy": len(healthy_indexers),
            "total": len(indexers),
            "status": indexer_status,
            "items": [
                {
                    "id": str(indexer.id),
                    "name": indexer.name,
                    "scope": indexer.scope.value,
                    "enabled": indexer.enabled,
                    "health": indexer.health,
                    "last_checked_at": indexer.last_checked_at,
                    "last_success_at": indexer.last_success_at,
                    "latency_ms": indexer.latency_ms,
                    "last_error": indexer.last_error,
                }
                for indexer in indexers
            ],
        },
        "torrent_archive": {
            **archive_health,
            "archived": archive_complete,
            "tracked": archive_total,
            "missing_or_failed": max(0, archive_total - archive_complete),
        },
        "storage_roots": [
            {"id": str(root.id), "name": root.name, "status": root.last_health or "unknown", "checked_at": root.last_health_checked_at}
            for root in roots
        ],
    }
