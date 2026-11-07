"""Put torrents that left qBittorrent back from the archive, on a schedule.

A torrent missing from qBittorrent leaves its media unwatched: nothing checks
that its tracker still has it. When the archive holds its .torrent, this job
adds it back stopped and has qBittorrent check the files already on disk. A
later run starts it once the check shows every file complete; a short check
leaves it stopped, so nothing is ever downloaded again.

Each torrent is re-added once. If it is removed again after that, the removal
was deliberate, and the title stays in Replace Media for the user to decide.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.integrations.qbittorrent import QBittorrentClient
from app.models.domain import Job, JobStatus, Severity, Torrent, TorrentArchiveState
from app.services.events import create_event
from app.services.integration_state import ConfiguredDownloadClient, list_configured_download_clients
from app.services.jobs import checkpoint_job, run_job, update_job
from app.services.torrent_archive import QBittorrentClientFactory, read_manifest, restore_torrent_archive

READD_JOB_TYPE = "readd_missing_torrents"

CHECKING = "checking"
SEEDING = "seeding"
FILES_MISSING = "files_missing"
FAILED = "failed"

#: An observation this soon after the re-add may predate qBittorrent starting
#: its check, so it proves nothing about the files.
SETTLE_AFTER = timedelta(minutes=2)
_BUSY_STATES = ("checking", "moving", "queuedforchecking")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def check_outcome(torrent, now: datetime) -> str | None:
    """What the latest qBittorrent observation says about a re-added torrent.

    SEEDING when its files are complete, FILES_MISSING when the check finished
    short, or None while there is nothing conclusive yet.
    """

    since = _aware(torrent.auto_readded_at)
    if since is None:
        return None
    seen = [
        item for item in torrent.observations
        if item.is_present and _aware(item.last_seen_at) and _aware(item.last_seen_at) >= since + SETTLE_AFTER
    ]
    if not seen:
        return None
    latest = max(seen, key=lambda item: _aware(item.last_seen_at))
    state = str(latest.state or "").casefold()
    if latest.progress is None or state.startswith(_BUSY_STATES):
        return None
    return SEEDING if float(latest.progress) >= 1 else FILES_MISSING


async def _start(client: ConfiguredDownloadClient, info_hash: str, client_factory: QBittorrentClientFactory) -> None:
    adapter = client_factory(client.url, client.username or "", client.password or "")
    try:
        await adapter.start_torrents([info_hash])
    finally:
        await adapter.close()


async def settle_checks(db: AsyncSession, *, client_factory: QBittorrentClientFactory, now: datetime) -> dict[str, int]:
    """Start re-added torrents whose files checked complete; flag the rest."""

    counts = {"started": 0, "files_missing": 0}
    clients = {client.id: client for client in await list_configured_download_clients(db)}
    torrents = (await db.scalars(
        select(Torrent).options(selectinload(Torrent.observations)).where(Torrent.readd_state.in_([CHECKING, FILES_MISSING]))
    )).all()
    for torrent in torrents:
        outcome = check_outcome(torrent, now)
        if outcome is None or outcome == torrent.readd_state:
            continue
        if outcome == SEEDING:
            if torrent.readd_state == CHECKING and (torrent.metadata_json or {}).get("readd_start", True):
                present = next((item for item in torrent.observations if item.is_present), None)
                client = clients.get(present.download_client_id) if present else None
                if client is None or not client.enabled:
                    continue
                try:
                    await _start(client, torrent.info_hash, client_factory)
                except Exception:
                    continue
            torrent.readd_state = SEEDING
            counts["started"] += 1
            await create_event(
                db, "torrent.readd_verified", entity_type="torrent", entity_id=torrent.id,
                message=f"{torrent.name} was re-added from the archive and its files checked complete.",
                details={"info_hash": torrent.info_hash},
            )
        elif torrent.readd_state == CHECKING:
            torrent.readd_state = FILES_MISSING
            counts["files_missing"] += 1
            await create_event(
                db, "torrent.readd_files_missing", entity_type="torrent", entity_id=torrent.id,
                severity=Severity.WARNING,
                message=f"{torrent.name} was re-added from the archive, but its files on disk are incomplete. It was left stopped.",
                details={"info_hash": torrent.info_hash},
            )
        await db.commit()
    return counts


async def readd_missing(db: AsyncSession, *, client_factory: QBittorrentClientFactory, now: datetime) -> dict[str, int]:
    """Re-add, stopped and checking, every archived torrent Replace Media lists as missing."""

    from app.services.recovery import _client_for_manifest, _manifest_media_type, _restore_category, _restore_save_path, _restore_tags
    from app.services.replacements import replacement_targets

    counts = {"readded": 0, "failed": 0}
    clients = await list_configured_download_clients(db)
    for entry in await replacement_targets(db):
        if not entry.readdable:
            continue
        for torrent in entry.torrents:
            if torrent.auto_readded_at is not None or torrent.archive_state != TorrentArchiveState.ARCHIVED or not torrent.archive_path:
                continue
            manifest = read_manifest(torrent)
            client, observation, _ = _client_for_manifest(manifest, clients, _manifest_media_type(manifest))
            save_path = _restore_save_path(manifest, observation)
            try:
                if client is None or not save_path:
                    raise AppError("READD_UNRESOLVED", "No qBittorrent client or save path could be worked out from the archive.")
                result = await restore_torrent_archive(
                    db, torrent.id, client.id, save_path,
                    category=_restore_category(observation, client), tags=_restore_tags(observation, client),
                    client_factory=client_factory, verify_first=True,
                )
            except AppError as exc:
                if exc.code == "TORRENT_ALREADY_PRESENT":
                    continue
                if torrent.readd_state != FAILED:
                    torrent.readd_state = FAILED
                    await create_event(
                        db, "torrent.readd_failed", entity_type="torrent", entity_id=torrent.id, severity=Severity.WARNING,
                        message=f"{torrent.name} could not be re-added from the archive: {exc.message}",
                        details={"info_hash": torrent.info_hash, "code": exc.code},
                    )
                counts["failed"] += 1
                await db.commit()
                continue
            torrent.auto_readded_at = now
            torrent.readd_state = CHECKING
            torrent.metadata_json = {**dict(torrent.metadata_json or {}), "readd_start": bool(result.get("start_after_check", True))}
            counts["readded"] += 1
            await db.commit()
    return counts


async def run_readd_missing_torrents(job_id, *, client_factory: QBittorrentClientFactory = QBittorrentClient) -> None:
    async def worker(db: AsyncSession, job: Job) -> None:
        await checkpoint_job(db, job, status=JobStatus.RUNNING,
                             progress={"stage": "checking", "current": 0, "total": 2, "percent": 0, "detail": "Checking re-added torrents…"})
        now = _utcnow()
        settled = await settle_checks(db, client_factory=client_factory, now=now)
        await checkpoint_job(db, job, progress={"stage": "re-adding", "current": 1, "total": 2, "percent": 50, "detail": "Re-adding missing torrents…"})
        added = await readd_missing(db, client_factory=client_factory, now=now)
        parts = [f"{added['readded']} re-added", f"{settled['started']} verified and started"]
        if settled["files_missing"]:
            parts.append(f"{settled['files_missing']} with missing files")
        if added["failed"]:
            parts.append(f"{added['failed']} could not be re-added")
        await update_job(
            db, job, status=JobStatus.COMPLETED,
            progress={"stage": "completed", "current": 2, "total": 2, "percent": 100, "detail": ", ".join(parts).capitalize() + "."},
            summary={**dict(job.summary or {}), **settled, **added},
        )

    await run_job(job_id, worker, failure_code="READD_FAILED", failure_message="Re-adding missing torrents failed.",
                  failure_progress={"stage": "failed", "percent": 100})
