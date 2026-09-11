from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    InteractiveSearchResult,
    MediaType,
    ReleaseBlocklist,
    Severity,
    Torrent,
    TorrentClientObservation,
    UpgradeCandidate,
)
from app.services.events import create_event


TERMINAL_INCOMPLETE_STATES = frozenset({"error", "missingfiles"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalized_title(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def terminal_incomplete_failure(observation: TorrentClientObservation, torrent: Torrent) -> bool:
    state = str(observation.state or "").strip().casefold()
    progress = float(observation.progress or 0)
    completed = torrent.completed_at is not None or bool((torrent.metadata_json or {}).get("completed"))
    return state in TERMINAL_INCOMPLETE_STATES and progress < 1 and not completed


async def matching_blocklist_entry(
    db: AsyncSession,
    *,
    media_type: MediaType,
    target_entity_type: str,
    target_entity_id: UUID | None,
    source_title: str,
    indexer_id: UUID | None = None,
    indexer_name: str | None = None,
    info_hash: str | None = None,
) -> ReleaseBlocklist | None:
    rows = (
        await db.scalars(
            select(ReleaseBlocklist).where(
                ReleaseBlocklist.media_type == media_type,
                ReleaseBlocklist.target_entity_type == target_entity_type,
                ReleaseBlocklist.target_entity_id == target_entity_id,
            )
        )
    ).all()
    wanted_hash = str(info_hash or "").strip().casefold()
    wanted_title = normalized_title(source_title)
    wanted_indexer = normalized_title(indexer_name)
    for row in rows:
        if wanted_hash and row.info_hash and row.info_hash.casefold() == wanted_hash:
            return row
        same_indexer = (
            indexer_id is not None and row.indexer_id == indexer_id
        ) or (
            bool(wanted_indexer) and normalized_title(row.indexer_name) == wanted_indexer
        )
        if normalized_title(row.source_title) == wanted_title and same_indexer:
            return row
    return None


async def blocklist_for_results(
    db: AsyncSession, rows: list[InteractiveSearchResult]
) -> dict[UUID, ReleaseBlocklist]:
    if not rows:
        return {}
    entries = (await db.scalars(select(ReleaseBlocklist))).all()
    matched: dict[UUID, ReleaseBlocklist] = {}
    for result in rows:
        for entry in entries:
            if entry.media_type != result.media_type:
                continue
            if entry.target_entity_type != result.target_entity_type or entry.target_entity_id != result.target_entity_id:
                continue
            if normalized_title(entry.source_title) != normalized_title(result.title):
                continue
            if entry.indexer_id == result.indexer_id or normalized_title(entry.indexer_name) == normalized_title(result.indexer_name):
                matched[result.id] = entry
                break
    return matched


async def _selected_origin(
    db: AsyncSession, torrent: Torrent, observation: TorrentClientObservation
) -> InteractiveSearchResult | UpgradeCandidate | None:
    selected = await db.scalar(
        select(InteractiveSearchResult)
        .where(
            func.lower(InteractiveSearchResult.submitted_info_hash) == torrent.info_hash.casefold(),
            InteractiveSearchResult.selected_download_client_id == observation.download_client_id,
            InteractiveSearchResult.selected_at.is_not(None),
        )
        .order_by(InteractiveSearchResult.selected_at.desc())
        .limit(1)
    )
    if selected is not None:
        return selected
    return await db.scalar(
        select(UpgradeCandidate)
        .where(
            func.lower(UpgradeCandidate.submitted_info_hash) == torrent.info_hash.casefold(),
            UpgradeCandidate.selected_download_client_id == observation.download_client_id,
            UpgradeCandidate.submitted_at.is_not(None),
        )
        .order_by(UpgradeCandidate.submitted_at.desc())
        .limit(1)
    )


def _same_payload(first: str | None, second: str | None) -> bool:
    left = str(first or "").strip().rstrip("/\\").casefold()
    right = str(second or "").strip().rstrip("/\\").casefold()
    return bool(left and right and left == right)


async def _payload_is_shared(db: AsyncSession, observation: TorrentClientObservation) -> bool:
    if not observation.resolved_save_path:
        return True
    others = (
        await db.scalars(
            select(TorrentClientObservation).where(
                TorrentClientObservation.id != observation.id,
                TorrentClientObservation.is_present.is_(True),
            )
        )
    ).all()
    return any(_same_payload(observation.resolved_save_path, other.resolved_save_path) for other in others)


def _origin_fields(origin: InteractiveSearchResult | UpgradeCandidate) -> tuple[MediaType, str, UUID | None, str, UUID | None, str | None, str | None]:
    if isinstance(origin, InteractiveSearchResult):
        return (
            origin.media_type,
            origin.target_entity_type,
            origin.target_entity_id,
            origin.title,
            origin.indexer_id,
            origin.indexer_name,
            origin.quality,
        )
    target_type = "movie" if origin.media_type == MediaType.MOVIES else "season"
    return (
        origin.media_type,
        target_type,
        origin.movie_id or origin.season_id,
        origin.title,
        origin.indexer_id,
        origin.indexer_name,
        origin.quality,
    )


async def handle_failed_download(
    db: AsyncSession,
    *,
    torrent: Torrent,
    observation: TorrentClientObservation,
    client_name: str,
    adapter,
) -> None:
    """Block and clean one terminal, incomplete, Medialogue-submitted download."""

    if not terminal_incomplete_failure(observation, torrent):
        return None
    handled = dict((torrent.metadata_json or {}).get("failed_download_clients") or {})
    marker = str(observation.download_client_id)
    previous_attempt = handled.get(marker)
    if previous_attempt is not None and (
        not isinstance(previous_attempt, dict)
        or previous_attempt.get("cleanup_succeeded", True)
    ):
        return None
    origin = await _selected_origin(db, torrent, observation)
    if origin is None:
        return None

    shared = await _payload_is_shared(db, observation)
    delete_files = not shared
    cleanup_error: str | None = None
    try:
        await adapter.remove_torrent(torrent.info_hash, delete_files=delete_files)
        observation.is_present = False
        observation.removed_at = utcnow()
    except Exception as exc:
        cleanup_error = str(exc)

    media_type, target_type, target_id, source_title, indexer_id, indexer_name, quality = _origin_fields(origin)
    reason = f"qBittorrent reported terminal state '{observation.state}'."
    if cleanup_error:
        reason += f" Cleanup failed: {cleanup_error}"
    existing = await matching_blocklist_entry(
        db,
        media_type=media_type,
        target_entity_type=target_type,
        target_entity_id=target_id,
        source_title=source_title,
        indexer_id=indexer_id,
        indexer_name=indexer_name,
        info_hash=torrent.info_hash,
    )

    # A failed qBittorrent removal is retried by later polls, but it must not
    # manufacture another failure event or blocklist entry each time.
    if previous_attempt is not None:
        if cleanup_error is None and existing is not None:
            existing.reason = reason
        handled[marker] = {
            "at": utcnow().isoformat(),
            "state": observation.state,
            "cleanup_succeeded": cleanup_error is None,
        }
        torrent.metadata_json = {**(torrent.metadata_json or {}), "failed_download_clients": handled}
        return None

    event = await create_event(
        db,
        "download.failed",
        entity_type="torrent",
        entity_id=torrent.id,
        severity=Severity.ERROR,
        message=f"Download failed: {source_title}.",
        details={
            "client_id": str(observation.download_client_id),
            "client_name": client_name,
            "info_hash": torrent.info_hash,
            "state": observation.state,
            "progress": float(observation.progress or 0),
            "delete_files": delete_files,
            "shared_payload": shared,
            "cleanup_error": cleanup_error,
        },
    )
    if existing is None:
        db.add(ReleaseBlocklist(
            media_type=media_type,
            target_entity_type=target_type,
            target_entity_id=target_id,
            torrent_id=torrent.id,
            info_hash=torrent.info_hash.casefold(),
            source_title=source_title,
            indexer_id=indexer_id,
            indexer_name=indexer_name,
            quality=quality,
            reason=reason,
            failure_event_id=event.id,
        ))
    handled[marker] = {
        "at": utcnow().isoformat(),
        "state": observation.state,
        "cleanup_succeeded": cleanup_error is None,
    }
    torrent.metadata_json = {**(torrent.metadata_json or {}), "failed_download_clients": handled}
    return None


def clear_recovered_failure_marker(torrent: Torrent, observation: TorrentClientObservation) -> None:
    if terminal_incomplete_failure(observation, torrent):
        return
    handled = dict((torrent.metadata_json or {}).get("failed_download_clients") or {})
    if handled.pop(str(observation.download_client_id), None) is not None:
        metadata = dict(torrent.metadata_json or {})
        metadata["failed_download_clients"] = handled
        torrent.metadata_json = metadata
