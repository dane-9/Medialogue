from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Job, JobStatus, StorageRoot
from app.services.indexers import refresh_indexer_health
from app.services.integration_state import (
    get_configured_plex,
    get_configured_tmdb,
    list_configured_indexers,
)
from app.services.jobs import checkpoint_job, run_job, update_job
from app.services.plex import refresh_plex_health
from app.services.qbittorrent import poll_due_download_clients
from app.services.reconciliation import mark_root_available, mark_root_unavailable
from app.services.tmdb import refresh_tmdb_health
from app.services.torrent_archive import archive_mount_health


async def run_integration_health_sweep(job_id) -> None:
    """Refresh every enabled integration without depending on an open UI."""

    async def worker(db: AsyncSession, job: Job) -> None:
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"stage": "checking", "current": 0, "total": 1, "percent": 0, "detail": "Checking configured services…"},
        )

        checked = 0
        healthy = 0
        unavailable = 0
        skipped = 0
        results: dict[str, object] = {}

        plex = await get_configured_plex(db)
        if plex is not None and plex.enabled and plex.token:
            result = await refresh_plex_health(db, plex)
            results["plex"] = result
            checked += 1
            healthy += int(result.get("status") == "healthy")
            unavailable += int(result.get("status") != "healthy")
        else:
            skipped += 1

        tmdb = await get_configured_tmdb(db)
        if tmdb is not None and tmdb.enabled and tmdb.api_key:
            result = await refresh_tmdb_health(db, tmdb)
            results["tmdb"] = result
            checked += 1
            healthy += int(result.get("status") == "healthy")
            unavailable += int(result.get("status") != "healthy")
        else:
            skipped += 1

        indexer_results: list[dict[str, object]] = []
        for indexer in await list_configured_indexers(db):
            if not indexer.enabled or not indexer.api_key:
                skipped += 1
                continue
            result = await refresh_indexer_health(db, indexer)
            indexer_results.append({"id": str(indexer.id), "name": indexer.name, **result})
            checked += 1
            healthy += int(result.get("status") == "healthy")
            unavailable += int(result.get("status") != "healthy")
        results["indexers"] = indexer_results

        roots = (await db.scalars(select(StorageRoot).order_by(StorageRoot.name))).all()
        root_results: list[dict[str, object]] = []
        for root in roots:
            if not root.enabled:
                skipped += 1
                continue
            try:
                reachable = Path(root.resolved_root_path).is_dir()
            except OSError:
                reachable = False
            if reachable:
                await mark_root_available(db, root)
            else:
                await mark_root_unavailable(db, root)
            root_results.append({"id": str(root.id), "name": root.name, "status": "available" if reachable else "unavailable"})
            checked += 1
            healthy += int(reachable)
            unavailable += int(not reachable)
        results["storage_roots"] = root_results

        # qBittorrent's observation poll is also its connectivity probe. Calling
        # the due-only entry point here never duplicates a client poll and keeps
        # the authentication-ban safeguard in one place.
        qbit_results = await poll_due_download_clients(db)
        results["qbittorrent"] = qbit_results
        results["torrent_archive"] = archive_mount_health()

        summary = {
            **dict(job.summary or {}),
            "checked": checked,
            "healthy": healthy,
            "unavailable": unavailable,
            "skipped": skipped,
            "results": results,
        }
        await update_job(
            db,
            job,
            status=JobStatus.COMPLETED,
            progress={"stage": "completed", "current": checked, "total": checked, "percent": 100, "detail": f"Checked {checked} configured services."},
            summary=summary,
        )

    await run_job(
        job_id,
        worker,
        failure_code="HEALTH_SWEEP_FAILED",
        failure_message="Integration health sweep failed.",
        failure_progress={"stage": "failed", "percent": 100},
    )
