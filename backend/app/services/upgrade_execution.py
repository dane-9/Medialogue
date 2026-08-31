from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import sign_confirmation_payload, verify_confirmation_payload
from app.integrations.qbittorrent import magnet_info_hash, torrent_payload_info_hash
from app.models.domain import (
    AccessMode,
    AssociationType,
    Episode,
    EpisodeMediaMap,
    MediaDirectory,
    MediaFile,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    QualityDefinition,
    ReleaseScope,
    ReleaseState,
    Season,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    StorageRoot,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
    UpgradeCandidate,
)
from app.parser import parse_release
from app.services.events import create_event, publish_live_event
from app.services.integration_state import get_configured_download_client, get_configured_indexer


QBittorrentClientFactory = Callable[..., Any]
TorznabClientFactory = Callable[..., Any]

ACTIVE_EXECUTION_STATES = frozenset({"submitted", "downloading", "validating", "handoff_attention", "cleanup_required"})
CONFIRMATION_TTL_SECONDS = 10 * 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def execution_active(row: UpgradeCandidate) -> bool:
    return bool(row.execution_state in ACTIVE_EXECUTION_STATES)


def _ids(values: list[str] | None) -> list[UUID]:
    result: list[UUID] = []
    for value in values or []:
        try:
            result.append(UUID(str(value)))
        except ValueError:
            continue
    return result


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


async def _current_releases(db: AsyncSession, row: UpgradeCandidate) -> list[MovieRelease | ShowRelease]:
    release_ids = _ids(row.current_release_ids)
    if not release_ids:
        raise AppError("UPGRADE_TARGET_STALE", "The current release for this upgrade no longer exists.", status_code=409)
    model = MovieRelease if row.media_type == MediaType.MOVIES else ShowRelease
    releases = list((await db.scalars(select(model).where(model.id.in_(release_ids)))).all())
    if len(releases) != len(release_ids):
        raise AppError("UPGRADE_TARGET_STALE", "The current release changed since this candidate was found.", status_code=409)
    if any(release.release_state != ReleaseState.CURRENT for release in releases):
        raise AppError("UPGRADE_TARGET_STALE", "The current release changed since this candidate was found. Search for upgrades again.", status_code=409)
    return releases


async def _assert_writable_target(db: AsyncSession, row: UpgradeCandidate, releases: list[MovieRelease | ShowRelease]) -> None:
    release_ids = [release.id for release in releases]
    root_ids: set[UUID] = set()
    if row.media_type == MediaType.MOVIES:
        root_ids.update(
            root_id
            for root_id in await db.scalars(
                select(MediaDirectory.storage_root_id).where(
                    MediaDirectory.movie_release_id.in_(release_ids),
                    MediaDirectory.exists.is_(True),
                    MediaDirectory.storage_root_id.is_not(None),
                )
            )
            if root_id is not None
        )
    else:
        root_ids.update(
            root_id
            for root_id in await db.scalars(
                select(MediaDirectory.storage_root_id)
                .join(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .where(
                    EpisodeMediaMap.show_release_id.in_(release_ids),
                    MediaFile.exists.is_(True),
                    MediaDirectory.storage_root_id.is_not(None),
                )
            )
            if root_id is not None
        )
    if not root_ids:
        raise AppError("UPGRADE_TARGET_STALE", "The current media path is no longer available for replacement.", status_code=409)
    roots = list((await db.scalars(select(StorageRoot).where(StorageRoot.id.in_(root_ids)))).all())
    if len(roots) != len(root_ids) or any(not root.enabled or root.access_mode != AccessMode.READ_WRITE for root in roots):
        raise AppError("ROOT_READ_ONLY", "Upgrades require the current media folder to be enabled and read/write.", status_code=409)


async def _old_live_torrents(
    db: AsyncSession,
    row: UpgradeCandidate,
    *,
    exclude_torrent_id: UUID | None = None,
) -> list[tuple[Torrent, TorrentClientObservation]]:
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
    unique: dict[tuple[UUID, UUID], tuple[Torrent, TorrentClientObservation]] = {}
    for torrent, observation in rows:
        unique[(torrent.id, observation.download_client_id)] = (torrent, observation)
    return list(unique.values())


async def _assert_old_torrents_archived(db: AsyncSession, row: UpgradeCandidate) -> None:
    for torrent, _observation in await _old_live_torrents(db, row):
        if torrent.archive_state != TorrentArchiveState.ARCHIVED:
            raise AppError(
                "TORRENT_ARCHIVE_REQUIRED",
                f"The current torrent {torrent.info_hash} must be backed up before Medialogue can upgrade it.",
                status_code=409,
            )


async def submit_upgrade_candidate(
    db: AsyncSession,
    candidate_id: UUID,
    *,
    download_client_id: UUID,
    category: str | None,
    torznab_client_factory: TorznabClientFactory,
    qbittorrent_client_factory: QBittorrentClientFactory,
) -> UpgradeCandidate:
    row = await db.scalar(select(UpgradeCandidate).where(UpgradeCandidate.id == candidate_id).with_for_update())
    if row is None:
        raise AppError("NOT_FOUND", "Upgrade candidate was not found.", status_code=404)
    if execution_active(row):
        raise AppError("UPGRADE_ALREADY_STARTED", "This upgrade is already in progress.", status_code=409)
    if not row.execution_eligible:
        raise AppError("UPGRADE_NOT_ELIGIBLE", row.execution_block_reason or "This upgrade cannot be executed.", status_code=409)
    if not row.download_url:
        raise AppError("UPGRADE_NOT_DOWNLOADABLE", "The indexer did not provide a downloadable torrent for this candidate.", status_code=409)

    releases = await _current_releases(db, row)
    await _assert_writable_target(db, row, releases)
    await _assert_old_torrents_archived(db, row)

    indexer = await get_configured_indexer(db, row.indexer_id) if row.indexer_id else None
    if indexer is None or not indexer.enabled or not indexer.api_key:
        raise AppError("INDEXER_UNAVAILABLE", "The indexer that supplied this upgrade is no longer available.", status_code=409)
    client = await get_configured_download_client(db, download_client_id)
    if client is None or not client.enabled:
        raise AppError("DOWNLOAD_CLIENT_UNAVAILABLE", "The selected qBittorrent client is unavailable.", status_code=404)
    if client.scope != row.media_type:
        raise AppError("DOWNLOAD_CLIENT_SCOPE_MISMATCH", f"{client.name} is not configured for {row.media_type.value}.", status_code=409)
    if indexer.download_client_ids and client.id not in indexer.download_client_ids:
        raise AppError("DOWNLOAD_CLIENT_NOT_ALLOWED_BY_INDEXER", f"{indexer.name} is not configured to grab through {client.name}.", status_code=409)

    selected_category = category.strip() if category and category.strip() else (client.category or None)
    adapter = qbittorrent_client_factory(client.url, client.username or "", client.password or "")
    torznab = None
    submitted_info_hash: str | None = None
    try:
        if selected_category is not None:
            categories = await adapter.list_categories()
            if not any(item.name == selected_category for item in categories):
                raise AppError("QBITTORRENT_CATEGORY_NOT_FOUND", "The selected qBittorrent category no longer exists.", status_code=409)
        published_at = row.published_at
        if published_at is not None and published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        recent = bool(published_at and published_at >= utcnow() - timedelta(days=21))
        add_to_top = (client.recent_priority if recent else client.older_priority) == "first"
        options = {
            "category": selected_category,
            "tags": tuple(client.tags or []),
            "sequential_order": client.sequential_order,
            "first_last_first": client.first_last_first,
            "content_layout": client.content_layout,
            "add_to_top": add_to_top,
        }
        if row.download_url.casefold().startswith("magnet:"):
            submitted_info_hash = magnet_info_hash(row.download_url)
            await adapter.add_url(row.download_url, **options)
        else:
            torznab = torznab_client_factory(indexer.torznab_url, indexer.api_key, timeout=float(indexer.timeout_seconds))
            torrent_bytes = await torznab.fetch_torrent(row.download_url)
            submitted_info_hash = torrent_payload_info_hash(torrent_bytes)
            safe_name = "".join(char if char.isalnum() or char in " ._-" else "_" for char in row.title).strip()
            await adapter.add_torrent(torrent_bytes, filename=f"{safe_name[:180] or 'upgrade'}.torrent", **options)
    except AppError:
        raise
    except Exception as exc:
        raise AppError("UPGRADE_SUBMISSION_FAILED", f"Could not submit upgrade torrent: {exc}", status_code=503) from exc
    finally:
        if torznab is not None:
            await torznab.close()
        await adapter.close()

    now = utcnow()
    row.execution_state = "submitted"
    row.submitted_info_hash = submitted_info_hash
    row.selected_download_client_id = client.id
    row.selected_download_client_name = client.name
    row.selected_category = selected_category
    row.submitted_at = now
    row.torrent_id = None
    row.incoming_release_ids = []
    row.validated_at = None
    row.old_torrents_removed_at = None
    row.cleanup_completed_at = None
    row.execution_error = None
    await create_event(
        db,
        "upgrade.submitted",
        entity_type="upgrade_candidate",
        entity_id=row.id,
        message=f"Submitted upgrade {row.title} to {client.name}.",
        details={
            "candidate_id": str(row.id),
            "download_client_id": str(client.id),
            "category": selected_category,
            "current_release_ids": list(row.current_release_ids or []),
        },
    )
    publish_live_event("upgrade.execution", entity_type="upgrade_candidate", entity_id=row.id, data={"candidate_id": str(row.id), "state": row.execution_state})
    await db.flush()
    return row


async def associate_upgrade_torrent(
    db: AsyncSession,
    torrent: Torrent,
    *,
    download_client_id: UUID,
    scope: MediaType,
    complete: bool,
) -> dict[str, Any] | None:
    rows = list(
        (
            await db.scalars(
                select(UpgradeCandidate)
                .where(
                    UpgradeCandidate.media_type == scope,
                    UpgradeCandidate.selected_download_client_id == download_client_id,
                    UpgradeCandidate.execution_state.in_(["submitted", "downloading", "validating", "handoff_attention"]),
                )
                .order_by(UpgradeCandidate.submitted_at.desc())
            )
        ).all()
    )
    normalized_hash = torrent.info_hash.casefold()
    row = next(
        (
            item
            for item in rows
            if item.torrent_id in {None, torrent.id}
            and item.submitted_info_hash
            and item.submitted_info_hash.casefold() == normalized_hash
        ),
        None,
    )
    if row is None:
        row = next(
            (
                item
                for item in rows
                if item.torrent_id in {None, torrent.id}
                and not item.submitted_info_hash
                and item.title.casefold() == torrent.name.casefold()
            ),
            None,
        )
    if row is None:
        return None

    incoming_ids = _ids(row.incoming_release_ids)
    if row.torrent_id is None:
        row.torrent_id = torrent.id
    elif row.torrent_id != torrent.id:
        return None

    if not incoming_ids:
        parsed = parse_release(torrent.name)
        quality = None
        if row.quality:
            quality = await db.scalar(select(QualityDefinition).where(QualityDefinition.name == row.quality))
        if row.media_type == MediaType.MOVIES:
            movie = await db.get(Movie, row.movie_id) if row.movie_id else None
            old_ids = _ids(row.current_release_ids)
            if movie is None or len(old_ids) != 1:
                row.execution_state = "handoff_attention"
                row.execution_error = "The Movie replacement target changed before qBittorrent reported the upgrade."
                return {"upgrade_execution_id": str(row.id)}
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name=torrent.name,
                parsed_title=parsed.identity.title_candidate or movie.title,
                parsed_year=parsed.identity.year or movie.year,
                parsed_edition=parsed.edition,
                effective_edition=parsed.edition,
                quality_definition_id=quality.id if quality else None,
                release_group=parsed.release_group,
                release_state=ReleaseState.MISSING,
                parser_version=parsed.parser_version,
                parse_snapshot={
                    **parsed.to_dict(),
                    "incoming": True,
                    "incoming_kind": "replacement",
                    "replacement_of_release_id": str(old_ids[0]),
                    "upgrade_candidate_id": str(row.id),
                },
                original_custom_format_score=row.custom_format_score,
                current_custom_format_score=row.custom_format_score,
            )
            db.add(release)
            await db.flush()
            db.add(MovieReleaseTorrent(movie_release_id=release.id, torrent_id=torrent.id, association_type=AssociationType.INCOMING))
            incoming_ids = [release.id]
        else:
            season = await db.get(Season, row.season_id) if row.season_id else None
            show = await db.get(Show, season.show_id) if season is not None else None
            if season is None or show is None:
                row.execution_state = "handoff_attention"
                row.execution_error = "The Show season replacement target changed before qBittorrent reported the upgrade."
                return {"upgrade_execution_id": str(row.id)}
            release = ShowRelease(
                show_id=show.id,
                season_id=season.id,
                raw_release_name=torrent.name,
                release_scope=ReleaseScope.SEASON_PACK,
                quality_definition_id=quality.id if quality else None,
                release_group=parsed.release_group,
                release_state=ReleaseState.MISSING,
                parse_snapshot={
                    **parsed.to_dict(),
                    "incoming": True,
                    "incoming_kind": "replacement",
                    "replacement_of_release_ids": list(row.current_release_ids or []),
                    "upgrade_candidate_id": str(row.id),
                },
                original_custom_format_score=row.custom_format_score,
                current_custom_format_score=row.custom_format_score,
            )
            db.add(release)
            await db.flush()
            db.add(ShowReleaseTorrent(show_release_id=release.id, torrent_id=torrent.id, association_type=AssociationType.INCOMING))
            incoming_ids = [release.id]
        row.incoming_release_ids = [str(value) for value in incoming_ids]
        await create_event(
            db,
            "upgrade.download_detected",
            entity_type="upgrade_candidate",
            entity_id=row.id,
            message=f"qBittorrent reported the selected upgrade: {torrent.name}.",
            details={"candidate_id": str(row.id), "torrent_id": str(torrent.id), "incoming_release_ids": list(row.incoming_release_ids)},
        )

    row.execution_state = "validating" if complete else "downloading"
    row.execution_error = None
    publish_live_event("upgrade.execution", entity_type="upgrade_candidate", entity_id=row.id, data={"candidate_id": str(row.id), "state": row.execution_state, "torrent_id": str(torrent.id)})
    return {"upgrade_execution_id": str(row.id), "incoming_release_ids": [str(value) for value in incoming_ids]}


async def mark_upgrade_torrent_removed(db: AsyncSession, torrent: Torrent) -> None:
    row = await db.scalar(
        select(UpgradeCandidate).where(
            UpgradeCandidate.torrent_id == torrent.id,
            UpgradeCandidate.execution_state.in_(["submitted", "downloading", "validating"]),
        )
    )
    if row is None:
        return
    row.execution_state = "handoff_attention"
    row.execution_error = "The replacement torrent was removed from qBittorrent before the upgrade handoff completed."
    publish_live_event("upgrade.execution", entity_type="upgrade_candidate", entity_id=row.id, data={"candidate_id": str(row.id), "state": row.execution_state})


async def _validate_show_replacement(db: AsyncSession, row: UpgradeCandidate, incoming_ids: list[UUID]) -> tuple[bool, str | None]:
    if row.season_id is None:
        return False, "The target season no longer exists."
    expected = set(
        await db.scalars(select(Episode.id).where(Episode.season_id == row.season_id, Episode.monitored.is_(True)))
    )
    mapped = set(
        await db.scalars(
            select(EpisodeMediaMap.episode_id)
            .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
            .where(EpisodeMediaMap.show_release_id.in_(incoming_ids), MediaFile.exists.is_(True))
        )
    )
    missing = expected - mapped
    if missing:
        return False, f"The replacement season pack is complete in qBittorrent but {len(missing)} monitored episode(s) were not validated on disk."
    return True, None


async def advance_upgrade_handoff(
    db: AsyncSession,
    torrent: Torrent,
    *,
    qbittorrent_client_factory: QBittorrentClientFactory,
) -> UpgradeCandidate | None:
    row = await db.scalar(
        select(UpgradeCandidate).where(
            UpgradeCandidate.torrent_id == torrent.id,
            UpgradeCandidate.execution_state.in_(["downloading", "validating", "handoff_attention"]),
        ).with_for_update()
    )
    if row is None:
        return None
    incoming_ids = _ids(row.incoming_release_ids)
    if not incoming_ids:
        row.execution_state = "handoff_attention"
        row.execution_error = "The replacement completed without a validated incoming release association."
        return row
    if torrent.archive_state != TorrentArchiveState.ARCHIVED:
        row.execution_state = "handoff_attention"
        row.execution_error = "The replacement completed, but its torrent backup is not complete. The current torrent was kept."
        return row

    if row.media_type == MediaType.MOVIES:
        incoming = list((await db.scalars(select(MovieRelease).where(MovieRelease.id.in_(incoming_ids)))).all())
        if len(incoming) != len(incoming_ids) or any(item.release_state != ReleaseState.CURRENT for item in incoming):
            row.execution_state = "handoff_attention"
            row.execution_error = "The replacement completed but did not become the validated current Movie release."
            return row
        old = list((await db.scalars(select(MovieRelease).where(MovieRelease.id.in_(_ids(row.current_release_ids))))).all())
        if any(item.release_state != ReleaseState.REPLACED for item in old):
            row.execution_state = "handoff_attention"
            row.execution_error = "The replacement completed, but the previous Movie release was not safely superseded."
            return row
    else:
        incoming = list((await db.scalars(select(ShowRelease).where(ShowRelease.id.in_(incoming_ids)))).all())
        if len(incoming) != len(incoming_ids) or any(item.release_state != ReleaseState.CURRENT for item in incoming):
            row.execution_state = "handoff_attention"
            row.execution_error = "The replacement completed but did not become a validated current season pack."
            return row
        valid, error = await _validate_show_replacement(db, row, incoming_ids)
        if not valid:
            row.execution_state = "handoff_attention"
            row.execution_error = error
            return row
        old = list((await db.scalars(select(ShowRelease).where(ShowRelease.id.in_(_ids(row.current_release_ids))))).all())
        for release in old:
            release.release_state = ReleaseState.REPLACED

    now = utcnow()
    row.validated_at = row.validated_at or now
    live_old = await _old_live_torrents(db, row, exclude_torrent_id=torrent.id)
    for old_torrent, _observation in live_old:
        if old_torrent.archive_state != TorrentArchiveState.ARCHIVED:
            row.execution_state = "handoff_attention"
            row.execution_error = f"Old torrent {old_torrent.info_hash} is no longer safely archived, so it was kept in qBittorrent."
            return row

    failures: list[str] = []
    for old_torrent, observation in live_old:
        client = await get_configured_download_client(db, observation.download_client_id)
        if client is None or not client.enabled:
            failures.append(f"qBittorrent client {observation.download_client_id} is unavailable")
            continue
        adapter = None
        try:
            adapter = qbittorrent_client_factory(client.url, client.username or "", client.password or "")
            await adapter.remove_torrent(old_torrent.info_hash, delete_files=False)
            observation.is_present = False
            observation.removed_at = now
            await create_event(
                db,
                "upgrade.old_torrent_removed",
                entity_type="torrent",
                entity_id=old_torrent.id,
                message="Removed the superseded torrent from qBittorrent without deleting media files.",
                details={"candidate_id": str(row.id), "client_id": str(client.id), "info_hash": old_torrent.info_hash, "delete_files": False},
            )
        except Exception as exc:
            failures.append(f"{client.name}: {exc}")
        finally:
            if adapter is not None:
                await adapter.close()
    if failures:
        row.execution_state = "handoff_attention"
        row.execution_error = "Replacement validated, but the old torrent could not be removed: " + "; ".join(failures)
        return row

    row.old_torrents_removed_at = now
    row.execution_state = "cleanup_required"
    row.execution_error = None
    await create_event(
        db,
        "upgrade.replacement_validated",
        entity_type="upgrade_candidate",
        entity_id=row.id,
        message="Replacement validated and old qBittorrent torrent removed. Old media is awaiting explicit cleanup confirmation.",
        details={"candidate_id": str(row.id), "torrent_id": str(torrent.id), "old_release_ids": list(row.current_release_ids or [])},
    )
    publish_live_event("upgrade.execution", entity_type="upgrade_candidate", entity_id=row.id, data={"candidate_id": str(row.id), "state": row.execution_state})
    return row


async def retry_upgrade_handoff(
    db: AsyncSession,
    candidate_id: UUID,
    *,
    qbittorrent_client_factory: QBittorrentClientFactory,
) -> UpgradeCandidate:
    row = await db.get(UpgradeCandidate, candidate_id)
    if row is None:
        raise AppError("NOT_FOUND", "Upgrade candidate was not found.", status_code=404)
    if row.execution_state != "handoff_attention" or row.torrent_id is None:
        raise AppError("UPGRADE_HANDOFF_NOT_RETRYABLE", "This upgrade is not waiting for a handoff retry.", status_code=409)
    torrent = await db.get(Torrent, row.torrent_id)
    if torrent is None or torrent.completed_at is None:
        raise AppError("UPGRADE_REPLACEMENT_INCOMPLETE", "The replacement torrent is not complete.", status_code=409)
    updated = await advance_upgrade_handoff(db, torrent, qbittorrent_client_factory=qbittorrent_client_factory)
    assert updated is not None
    return updated


def _file_stat(path: Path) -> dict[str, Any]:
    stat = path.lstat()
    return {"path": str(path), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "symlink": path.is_symlink()}


def _directory_inventory(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(path.rglob("*"), key=lambda value: str(value).casefold()):
        try:
            if item.is_dir() and not item.is_symlink():
                continue
            rows.append(_file_stat(item))
        except FileNotFoundError:
            continue
    return rows


async def _replacement_cleanup_guard(db: AsyncSession, row: UpgradeCandidate) -> tuple[list[str], list[dict[str, Any]]]:
    """Reconfirm replacement media is still physically present before old-media deletion.

    The signed cleanup preview includes this evidence in its digest. If the
    replacement disappears or changes between preview and commit, the preview
    becomes stale and the destructive step is refused.
    """

    incoming_ids = _ids(row.incoming_release_ids)
    if not incoming_ids:
        return ["Replacement media association is unavailable."], []

    blocked: list[str] = []
    evidence: list[dict[str, Any]] = []
    if row.media_type == MediaType.MOVIES:
        directories = list(
            (
                await db.scalars(
                    select(MediaDirectory)
                    .options(selectinload(MediaDirectory.files))
                    .where(
                        MediaDirectory.movie_release_id.in_(incoming_ids),
                        MediaDirectory.exists.is_(True),
                    )
                )
            ).all()
        )
        if not directories:
            return ["Replacement Movie media is no longer present."], []
        for directory in directories:
            tracked = [media_file for media_file in directory.files if media_file.exists]
            if not tracked:
                blocked.append(f"{directory.resolved_path}: replacement has no validated media files")
                continue
            for media_file in tracked:
                path = Path(directory.resolved_path) / media_file.relative_path
                try:
                    if not path.exists() or not path.is_file():
                        blocked.append(f"{path}: replacement media is missing")
                        continue
                    evidence.append(_file_stat(path))
                except OSError:
                    blocked.append(f"{path}: replacement media could not be verified")
    else:
        if row.season_id is None:
            return ["Replacement season is no longer available."], []
        expected = set(
            await db.scalars(
                select(Episode.id).where(Episode.season_id == row.season_id, Episode.monitored.is_(True))
            )
        )
        rows = (
            await db.execute(
                select(EpisodeMediaMap.episode_id, MediaFile, MediaDirectory)
                .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
                .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
                .where(
                    EpisodeMediaMap.show_release_id.in_(incoming_ids),
                    MediaFile.exists.is_(True),
                )
            )
        ).all()
        verified_episodes: set[UUID] = set()
        seen_paths: set[str] = set()
        for episode_id, media_file, directory in rows:
            path = Path(directory.resolved_path) / media_file.relative_path
            try:
                if not path.exists() or not path.is_file():
                    continue
                verified_episodes.add(episode_id)
                key = str(path)
                if key not in seen_paths:
                    evidence.append(_file_stat(path))
                    seen_paths.add(key)
            except OSError:
                continue
        missing = expected - verified_episodes
        if missing:
            blocked.append(
                f"Replacement season media is missing for {len(missing)} monitored episode(s)."
            )

    if not evidence and not blocked:
        blocked.append("Replacement media could not be verified on disk.")
    return blocked, evidence


async def _cleanup_core(db: AsyncSession, row: UpgradeCandidate) -> dict[str, Any]:
    old_ids = _ids(row.current_release_ids)
    blocked, replacement_guard = await _replacement_cleanup_guard(db, row)
    targets: list[dict[str, Any]] = []
    media_file_ids: set[UUID] = set()

    if row.media_type == MediaType.MOVIES:
        directories = list(
            (
                await db.scalars(
                    select(MediaDirectory).where(MediaDirectory.movie_release_id.in_(old_ids), MediaDirectory.exists.is_(True))
                )
            ).all()
        )
        for directory in directories:
            root = await db.get(StorageRoot, directory.storage_root_id) if directory.storage_root_id else None
            path = Path(directory.resolved_path)
            if root is None or not root.enabled or root.access_mode != AccessMode.READ_WRITE:
                blocked.append(f"{path}: media folder is not read/write")
                continue
            root_path = Path(root.resolved_root_path)
            if not _inside(path, root_path) or path.resolve(strict=False) == root_path.resolve(strict=False) or path.is_symlink():
                blocked.append(f"{path}: unsafe deletion target")
                continue
            shared = await db.scalar(
                select(MediaDirectory.id)
                .where(
                    MediaDirectory.resolved_path == directory.resolved_path,
                    MediaDirectory.exists.is_(True),
                    MediaDirectory.movie_release_id.is_not(None),
                    MediaDirectory.movie_release_id.not_in(old_ids),
                )
                .limit(1)
            )
            if shared is not None:
                blocked.append(f"{path}: directory is shared with another release")
                continue
            inventory = _directory_inventory(path) if path.exists() else []
            targets.append({"path": str(path), "kind": "directory", "inventory": inventory})
    else:
        rows = (
            await db.execute(
                select(MediaFile, MediaDirectory)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
                .where(EpisodeMediaMap.show_release_id.in_(old_ids), MediaFile.exists.is_(True))
            )
        ).all()
        seen_files: set[UUID] = set()
        file_targets: dict[str, dict[str, Any]] = {}
        for media_file, directory in rows:
            if media_file.id in seen_files:
                continue
            seen_files.add(media_file.id)
            mapping_release_ids = set(
                value
                for value in await db.scalars(select(EpisodeMediaMap.show_release_id).where(EpisodeMediaMap.media_file_id == media_file.id))
                if value is not None
            )
            if not mapping_release_ids or not mapping_release_ids.issubset(set(old_ids)):
                blocked.append(f"{media_file.filename}: file is shared with another release")
                continue
            root = await db.get(StorageRoot, directory.storage_root_id) if directory.storage_root_id else None
            path = Path(directory.resolved_path) / media_file.relative_path
            if root is None or not root.enabled or root.access_mode != AccessMode.READ_WRITE:
                blocked.append(f"{path}: media folder is not read/write")
                continue
            root_path = Path(root.resolved_root_path)
            if not _inside(path, root_path) or path.resolve(strict=False) == root_path.resolve(strict=False):
                blocked.append(f"{path}: unsafe deletion target")
                continue
            media_file_ids.add(media_file.id)
            parent = path.parent
            prefix = f"{path.stem}."
            siblings = []
            if parent.exists():
                for sibling in parent.iterdir():
                    if sibling.is_file() or sibling.is_symlink():
                        if sibling.name == path.name or sibling.name.startswith(prefix):
                            siblings.append(sibling)
            if not siblings and path.exists():
                siblings = [path]
            for sibling in siblings:
                try:
                    file_targets[str(sibling)] = {"path": str(sibling), "kind": "file", "inventory": [_file_stat(sibling)]}
                except FileNotFoundError:
                    continue
        targets.extend(file_targets.values())

    total_bytes = sum(int(item.get("size") or 0) for target in targets for item in target.get("inventory") or [])
    return {
        "candidate_id": str(row.id),
        "media_type": row.media_type.value,
        "eligible": not blocked,
        "blocked": blocked,
        "targets": targets,
        "media_file_ids": [str(value) for value in sorted(media_file_ids, key=str)],
        "total_bytes": total_bytes,
        "_replacement_guard": replacement_guard,
    }


def _cleanup_digest(core: dict[str, Any]) -> str:
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def cleanup_preview(db: AsyncSession, candidate_id: UUID) -> dict[str, Any]:
    row = await db.get(UpgradeCandidate, candidate_id)
    if row is None:
        raise AppError("NOT_FOUND", "Upgrade candidate was not found.", status_code=404)
    if row.execution_state != "cleanup_required":
        raise AppError("UPGRADE_CLEANUP_NOT_READY", "Old media cleanup is only available after the replacement handoff is validated.", status_code=409)
    core = await _cleanup_core(db, row)
    expiry = int(time.time()) + CONFIRMATION_TTL_SECONDS
    payload = {
        "purpose": "upgrade_media_cleanup",
        "candidate_id": str(row.id),
        "inventory_hash": _cleanup_digest(core),
        "exp": expiry,
    }
    return {
        **core,
        "confirmation_token": sign_confirmation_payload(payload, get_settings().secret_key),
        "expires_at": datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat(),
        "warnings": (["Show cleanup removes only release media and same-stem sidecars; unrelated files are retained."] if row.media_type == MediaType.SHOWS else []),
    }


async def commit_cleanup(db: AsyncSession, candidate_id: UUID, confirmation_token: str) -> dict[str, Any]:
    payload = verify_confirmation_payload(confirmation_token, get_settings().secret_key)
    if payload is None or payload.get("purpose") != "upgrade_media_cleanup":
        raise AppError("INVALID_CONFIRMATION", "Upgrade cleanup confirmation is invalid.", status_code=409)
    try:
        token_candidate_id = UUID(str(payload["candidate_id"]))
        expiry = int(payload["exp"])
        digest = str(payload["inventory_hash"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("INVALID_CONFIRMATION", "Upgrade cleanup confirmation is malformed.", status_code=409) from exc
    if token_candidate_id != candidate_id:
        raise AppError("INVALID_CONFIRMATION", "Upgrade cleanup confirmation belongs to another candidate.", status_code=409)
    if expiry < int(time.time()):
        raise AppError("CONFIRMATION_EXPIRED", "Upgrade cleanup preview expired. Review the filesystem again.", status_code=409)

    row = await db.scalar(select(UpgradeCandidate).where(UpgradeCandidate.id == candidate_id).with_for_update())
    if row is None:
        raise AppError("NOT_FOUND", "Upgrade candidate was not found.", status_code=404)
    if row.execution_state != "cleanup_required":
        raise AppError("UPGRADE_CLEANUP_NOT_READY", "This upgrade is no longer waiting for media cleanup.", status_code=409)
    core = await _cleanup_core(db, row)
    if _cleanup_digest(core) != digest:
        raise AppError("DELETE_PREVIEW_STALE", "Old media changed since the cleanup preview. Review it again before deleting anything.", status_code=409)
    if core["blocked"]:
        raise AppError("UPGRADE_CLEANUP_BLOCKED", "Old media cleanup contains shared or unsafe paths and cannot be executed automatically.", status_code=409)

    deleted: list[str] = []
    for target in core["targets"]:
        path = Path(str(target["path"]))
        if not path.exists() and not path.is_symlink():
            continue
        if target["kind"] == "directory":
            await asyncio.to_thread(shutil.rmtree, path)
        else:
            await asyncio.to_thread(path.unlink)
        deleted.append(str(path))

    now = utcnow()
    old_ids = _ids(row.current_release_ids)
    if row.media_type == MediaType.MOVIES:
        releases = list((await db.scalars(select(MovieRelease).where(MovieRelease.id.in_(old_ids)))).all())
        directories = list(
            (
                await db.scalars(
                    select(MediaDirectory)
                    .options(selectinload(MediaDirectory.files))
                    .where(MediaDirectory.movie_release_id.in_(old_ids))
                )
            ).all()
        )
        for directory in directories:
            path = Path(directory.resolved_path)
            if not path.exists():
                directory.exists = False
                directory.missing_since = directory.missing_since or now
                directory.last_exists_check_at = now
                for media_file in directory.files:
                    media_file.exists = False
                    media_file.missing_since = media_file.missing_since or now
                    media_file.last_exists_check_at = now
        for release in releases:
            release.release_state = ReleaseState.REMOVED
            release.removed_at = release.removed_at or now
    else:
        media_file_ids = [UUID(value) for value in core.get("media_file_ids") or []]
        files = list((await db.scalars(select(MediaFile).where(MediaFile.id.in_(media_file_ids)))).all()) if media_file_ids else []
        for media_file in files:
            directory = await db.get(MediaDirectory, media_file.media_directory_id)
            if directory is None:
                continue
            path = Path(directory.resolved_path) / media_file.relative_path
            if not path.exists():
                media_file.exists = False
                media_file.missing_since = media_file.missing_since or now
                media_file.last_exists_check_at = now
        releases = list((await db.scalars(select(ShowRelease).where(ShowRelease.id.in_(old_ids)))).all())
        for release in releases:
            release.release_state = ReleaseState.REMOVED
        episode_ids = set(
            await db.scalars(select(EpisodeMediaMap.episode_id).where(EpisodeMediaMap.show_release_id.in_(old_ids)))
        )
        if episode_ids:
            from app.services.shows import refresh_episode_presence
            for episode_id in episode_ids:
                episode = await db.get(Episode, episode_id)
                if episode is not None:
                    await refresh_episode_presence(db, episode)

    row.cleanup_completed_at = now
    row.execution_state = "completed"
    row.execution_error = None
    await create_event(
        db,
        "upgrade.cleanup_completed",
        entity_type="upgrade_candidate",
        entity_id=row.id,
        message="Deleted explicitly reviewed superseded media after the replacement handoff.",
        details={"candidate_id": str(row.id), "deleted_paths": deleted, "old_release_ids": list(row.current_release_ids or [])},
    )
    publish_live_event("upgrade.execution", entity_type="upgrade_candidate", entity_id=row.id, data={"candidate_id": str(row.id), "state": row.execution_state})
    return {"candidate_id": row.id, "state": row.execution_state, "deleted_paths": deleted}
