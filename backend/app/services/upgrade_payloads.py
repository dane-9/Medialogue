"""Which media paths and live torrents an upgrade replaces, and the helpers both execution and cleanup share."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    EpisodeMediaMap,
    MediaDirectory,
    MediaFile,
    MediaType,
    MovieReleaseTorrent,
    ShowReleaseTorrent,
    Torrent,
    TorrentClientObservation,
    UpgradeCandidate,
)
from app.services.integration_state import get_configured_download_client


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ids(values: list[str] | None) -> list[UUID]:
    result: list[UUID] = []
    for value in values or []:
        try:
            result.append(UUID(str(value)))
        except ValueError:
            continue
    return result


def _path_key(path: Path) -> str:
    return str(path.resolve(strict=False)).casefold()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


async def _old_media_paths(db: AsyncSession, row: UpgradeCandidate) -> list[Path]:
    """Return the physical payload paths that the upgrade intends to retire."""

    release_ids = _ids(row.current_release_ids)
    if not release_ids:
        return []
    if row.media_type == MediaType.MOVIES:
        return [
            Path(value)
            for value in await db.scalars(
                select(MediaDirectory.resolved_path).where(
                    MediaDirectory.movie_release_id.in_(release_ids),
                    MediaDirectory.exists.is_(True),
                )
            )
        ]
    rows = (
        await db.execute(
            select(MediaFile.relative_path, MediaDirectory.resolved_path)
            .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
            .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
            .where(EpisodeMediaMap.show_release_id.in_(release_ids), MediaFile.exists.is_(True))
        )
    ).all()
    return [Path(directory_path) / relative_path for relative_path, directory_path in rows]


def _payload_file_identities(
    paths: list[Path],
    *,
    file_signatures: set[tuple[str, int]] | None = None,
) -> set[tuple[int, int]]:
    """Collect stable local file identities, including hard-linked payloads."""

    identities: set[tuple[int, int]] = set()
    for path in paths:
        candidates = [path]
        try:
            if path.is_dir() and not path.is_symlink():
                candidates = list(path.rglob("*"))
        except OSError:
            continue
        for candidate in candidates:
            try:
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                stat = candidate.stat()
                if file_signatures is not None and (candidate.name.casefold(), int(stat.st_size)) not in file_signatures:
                    continue
                identities.add((int(stat.st_dev), int(stat.st_ino)))
            except OSError:
                continue
    return identities


def _archived_file_signatures(torrent: Torrent, download_client_id: UUID) -> set[tuple[str, int]]:
    """Read qBittorrent's selected payload names/sizes from its archive."""

    from app.services.torrent_archive import manifest_for_client, read_manifest

    try:
        recovery = manifest_for_client(read_manifest(torrent), download_client_id).get("recovery") or {}
    except (OSError, TypeError, ValueError):
        return set()
    signatures: set[tuple[str, int]] = set()
    for item in recovery.get("files") or []:
        if not isinstance(item, dict):
            continue
        try:
            priority = int(item.get("priority", 1))
            size = int(item["size"])
        except (KeyError, TypeError, ValueError):
            continue
        name = Path(str(item.get("name") or "").replace("\\", "/")).name.casefold()
        if name and size >= 0 and priority != 0:
            signatures.add((name, size))
    return signatures


def _content_path_owns_payload(
    torrent: Torrent,
    observation: TorrentClientObservation,
    content_path: Path,
    old_paths: list[Path],
    old_identities: set[tuple[int, int]],
) -> bool:
    # The content root itself may be the outgoing directory, or a nested path
    # within it. Do not accept the inverse relationship on path alone: a broad
    # qBittorrent content root such as the whole Movies folder is merely an
    # ancestor and does not own every release below it. File identity proves
    # the ancestor-directory and hard-link cases below.
    if any(_inside(content_path, old_path) for old_path in old_paths):
        return True
    if not old_identities:
        return False
    signatures = _archived_file_signatures(torrent, observation.download_client_id)
    if not signatures:
        return False
    return bool(_payload_file_identities([content_path], file_signatures=signatures) & old_identities)


async def _old_live_torrents(
    db: AsyncSession,
    row: UpgradeCandidate,
    *,
    exclude_torrent_id: UUID | None = None,
) -> list[tuple[Torrent, TorrentClientObservation, bool]]:
    release_ids = _ids(row.current_release_ids)
    if not release_ids:
        return []
    association = MovieReleaseTorrent if row.media_type == MediaType.MOVIES else ShowReleaseTorrent
    release_column = association.movie_release_id if row.media_type == MediaType.MOVIES else association.show_release_id
    statement = (
        select(Torrent, TorrentClientObservation)
        .join(association, association.torrent_id == Torrent.id)
        .join(TorrentClientObservation, TorrentClientObservation.torrent_id == Torrent.id)
        .where(release_column.in_(release_ids), TorrentClientObservation.is_present.is_(True))
    )
    if exclude_torrent_id is not None:
        statement = statement.where(Torrent.id != exclude_torrent_id)
    rows = (await db.execute(statement)).all()
    unique: dict[tuple[UUID, UUID], tuple[Torrent, TorrentClientObservation, bool]] = {}
    for torrent, observation in rows:
        unique[(torrent.id, observation.download_client_id)] = (torrent, observation, False)

    # A torrent can own the same payload without having been linked to the
    # release successfully (different tracker naming, root folder, or a
    # hard-linked cross-seed). The qBittorrent content path is therefore an
    # independent ownership boundary before old media may be deleted.
    old_paths = await _old_media_paths(db, row)
    old_identities = await asyncio.to_thread(_payload_file_identities, old_paths)
    candidates = (
        await db.execute(
            select(Torrent, TorrentClientObservation)
            .join(TorrentClientObservation, TorrentClientObservation.torrent_id == Torrent.id)
            .where(TorrentClientObservation.is_present.is_(True))
        )
    ).all()
    incoming_ids = set(_ids(row.incoming_release_ids))
    incoming_torrent_ids: set[UUID] = set()
    if incoming_ids:
        if row.media_type == MediaType.MOVIES:
            incoming_torrent_ids.update(
                await db.scalars(
                    select(MovieReleaseTorrent.torrent_id).where(MovieReleaseTorrent.movie_release_id.in_(incoming_ids))
                )
            )
        else:
            incoming_torrent_ids.update(
                await db.scalars(
                    select(ShowReleaseTorrent.torrent_id).where(ShowReleaseTorrent.show_release_id.in_(incoming_ids))
                )
            )
    for torrent, observation in candidates:
        key = (torrent.id, observation.download_client_id)
        if key in unique or torrent.id == exclude_torrent_id or torrent.id in incoming_torrent_ids:
            continue
        # resolved_save_path falls back to qBittorrent's broad save directory
        # when no content_path was reported. Never infer ownership from that
        # fallback because it could encompass an entire library.
        if not str((torrent.metadata_json or {}).get("content_path") or "").strip():
            continue
        client = await get_configured_download_client(db, observation.download_client_id)
        if client is None or not client.enabled or client.scope != row.media_type:
            continue
        resolved = str(observation.resolved_save_path or "").strip()
        if resolved and await asyncio.to_thread(
            _content_path_owns_payload, torrent, observation, Path(resolved), old_paths, old_identities
        ):
            unique[key] = (torrent, observation, True)
    return list(unique.values())
