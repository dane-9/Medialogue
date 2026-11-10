from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.integrations.qbittorrent import (
    magnet_announce_hosts,
    magnet_info_hash,
    torrent_announce_hosts,
    torrent_payload_info_hash,
)
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
    UpgradeCandidate,
)
from app.parser import parse_release
from app.services.events import create_event, publish_live_event
from app.services.integration_state import get_configured_download_client, get_configured_indexer
from app.services.movie_editions import edition_label, same_edition
from app.services.tracker_hosts import remember_tracker_hosts
from app.services.upgrade_cleanup import (
    _block_automatic_cleanup,
    _cleanup_core,
)
from app.services.upgrade_payloads import _ids, _old_live_torrents, utcnow


QBittorrentClientFactory = Callable[..., Any]
TorznabClientFactory = Callable[..., Any]

ACTIVE_EXECUTION_STATES = frozenset({"submitted", "linking", "downloading", "validating", "handoff_attention", "cleanup_required"})


def execution_active(row: UpgradeCandidate) -> bool:
    return bool(row.execution_state in ACTIVE_EXECUTION_STATES)


def _candidate_edition(row: UpgradeCandidate) -> str | None:
    candidate = (row.comparison_snapshot or {}).get("candidate") or {}
    if isinstance(candidate, dict) and "edition" in candidate:
        value = candidate.get("edition")
        return str(value).strip() if value else None
    return parse_release(row.title).edition


def _movie_edition_error(current: str | None, candidate: str | None) -> str:
    return (
        "The replacement edition does not match the current Movie edition "
        f"({edition_label(current)} → {edition_label(candidate)}). Search for upgrades again."
    )


async def _current_releases(db: AsyncSession, row: UpgradeCandidate) -> list[MovieRelease | ShowRelease]:
    release_ids = _ids(row.current_release_ids)
    if not release_ids:
        raise AppError("UPGRADE_TARGET_STALE", "The current release for this upgrade no longer exists.", status_code=409)
    model = MovieRelease if row.media_type == MediaType.MOVIES else ShowRelease
    releases = list((await db.scalars(select(model).where(model.id.in_(release_ids)))).all())
    if len(releases) != len(release_ids):
        raise AppError("UPGRADE_TARGET_STALE", "The current release changed since this candidate was found.", status_code=409)
    if any(release.release_state not in ([ReleaseState.CURRENT, ReleaseState.MISSING] if row.target_key.startswith("replace:") else [ReleaseState.CURRENT]) for release in releases):
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


async def _assert_old_torrents_archived(db: AsyncSession, row: UpgradeCandidate) -> None:
    for torrent, _observation, _discovered_cross_seed in await _old_live_torrents(db, row):
        if torrent.archive_state != TorrentArchiveState.ARCHIVED:
            raise AppError(
                "TORRENT_ARCHIVE_REQUIRED",
                f"The current torrent {torrent.info_hash} must be backed up before Medialogue can upgrade it.",
                status_code=409,
            )


async def _assert_not_blocklisted(db: AsyncSession, row: UpgradeCandidate, info_hash: str | None = None) -> None:
    from app.services.blocklist import matching_blocklist_entry

    blocked = await matching_blocklist_entry(
        db,
        media_type=row.media_type,
        target_entity_type="movie" if row.media_type == MediaType.MOVIES else "season",
        target_entity_id=row.movie_id if row.media_type == MediaType.MOVIES else row.season_id,
        source_title=row.title,
        indexer_id=row.indexer_id,
        indexer_name=row.indexer_name,
        info_hash=info_hash,
    )
    if blocked is not None:
        raise AppError(
            "RELEASE_BLOCKLISTED",
            "This release previously failed. Remove it from the Blocklist before downloading it again.",
            status_code=409,
            details={"blocklist_id": str(blocked.id), "reason": blocked.reason},
        )


async def submit_upgrade_candidate(
    db: AsyncSession,
    candidate_id: UUID,
    *,
    download_client_id: UUID,
    category: str | None,
    delete_old_media: bool,
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
    await _assert_not_blocklisted(db, row)

    releases = await _current_releases(db, row)
    active = (await db.scalars(select(UpgradeCandidate).where(UpgradeCandidate.id != row.id,
        UpgradeCandidate.execution_state.in_(ACTIVE_EXECUTION_STATES)))).all()
    if any(set(other.current_release_ids or []) & set(row.current_release_ids or []) for other in active):
        raise AppError("REPLACEMENT_ALREADY_RUNNING", "Another upgrade or replacement is already handling this media.", status_code=409)
    if row.media_type == MediaType.MOVIES:
        current = releases[0]
        assert isinstance(current, MovieRelease)
        candidate_edition = _candidate_edition(row)
        if not same_edition(current.effective_edition, candidate_edition):
            raise AppError(
                "UPGRADE_EDITION_MISMATCH",
                _movie_edition_error(current.effective_edition, candidate_edition),
                status_code=409,
            )
    if row.target_key.startswith("replace:"):
        from app.services.replacements import revalidate_replacement
        await revalidate_replacement(db, row)
    else:
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
    if (row.comparison_snapshot or {}).get("same_file"):
        from app.services.same_file_links import submit_same_file_link

        return await submit_same_file_link(
            db, row, client=client, indexer=indexer, category=selected_category,
            torznab_client_factory=torznab_client_factory, qbittorrent_client_factory=qbittorrent_client_factory,
        )
    adapter = qbittorrent_client_factory(client.url, client.username or "", client.password or "")
    torznab = None
    submitted_info_hash: str | None = None
    announce_hosts: tuple[str, ...] = ()
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
            announce_hosts = magnet_announce_hosts(row.download_url)
            await _assert_not_blocklisted(db, row, submitted_info_hash)
            if row.target_key.startswith("replace:") and (not submitted_info_hash or submitted_info_hash.lower() in row.comparison_snapshot.get("excluded_hashes", [])):
                raise AppError("REPLACEMENT_SAME_TORRENT", "The indexer returned an existing or unidentified torrent. Choose another result.", status_code=409)
            await adapter.add_url(row.download_url, **options)
        else:
            torznab = torznab_client_factory(indexer.torznab_url, indexer.api_key, timeout=float(indexer.timeout_seconds))
            torrent_bytes = await torznab.fetch_torrent(row.download_url)
            submitted_info_hash = torrent_payload_info_hash(torrent_bytes)
            announce_hosts = torrent_announce_hosts(torrent_bytes)
            await _assert_not_blocklisted(db, row, submitted_info_hash)
            if row.target_key.startswith("replace:") and (not submitted_info_hash or submitted_info_hash.lower() in row.comparison_snapshot.get("excluded_hashes", [])):
                raise AppError("REPLACEMENT_SAME_TORRENT", "The indexer returned an existing or unidentified torrent. Choose another result.", status_code=409)
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
    row.comparison_snapshot = {
        **(row.comparison_snapshot or {}),
        # A trumped title's files are safe, so its replacement finishes like
        # an upgrade: every old torrent retired, the old media deleted.
        "delete_old_media_after_upgrade": bool(
            delete_old_media and (not row.target_key.startswith("replace:") or _finishes_like_upgrade(row))
        ),
    }
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
    await remember_tracker_hosts(db, indexer.id, announce_hosts)
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
                    UpgradeCandidate.execution_state.in_(["submitted", "linking", "downloading", "validating", "handoff_attention"]),
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
    if row.execution_state == "linking":
        # Only being checked against files already linked to their release. It
        # must not become an incoming release; settle_same_file_links decides.
        row.torrent_id = torrent.id
        return {"upgrade_execution_id": str(row.id)}

    incoming_ids = _ids(row.incoming_release_ids)
    if row.torrent_id is None:
        row.torrent_id = torrent.id
    elif row.torrent_id != torrent.id:
        return None
    torrent.metadata_json = {
        **(torrent.metadata_json or {}),
        "tracker_title": row.title,
        "indexer_name": row.indexer_name,
    }

    if not incoming_ids:
        parsed = parse_release(torrent.name)
        quality = None
        if row.quality:
            quality = await db.scalar(select(QualityDefinition).where(QualityDefinition.name == row.quality))
        if row.media_type == MediaType.MOVIES:
            movie = await db.get(Movie, row.movie_id) if row.movie_id else None
            old_ids = _ids(row.current_release_ids)
            old_release = await db.get(MovieRelease, old_ids[0]) if len(old_ids) == 1 else None
            if movie is None or old_release is None:
                row.execution_state = "handoff_attention"
                row.execution_error = "The Movie replacement target changed before qBittorrent reported the upgrade."
                return {"upgrade_execution_id": str(row.id)}
            expected_edition = _candidate_edition(row)
            if (
                not same_edition(old_release.effective_edition, expected_edition)
                or not same_edition(expected_edition, parsed.edition)
            ):
                row.execution_state = "handoff_attention"
                row.execution_error = _movie_edition_error(old_release.effective_edition, parsed.edition)
                return {"upgrade_execution_id": str(row.id)}
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name=torrent.name,
                tracker_title=row.title,
                parsed_title=parsed.identity.title_candidate or movie.title,
                parsed_year=parsed.identity.year or movie.year,
                parsed_edition=parsed.edition,
                effective_edition=parsed.edition,
                quality_definition_id=quality.id if quality else None,
                release_group=parsed.release_group,
                release_state=ReleaseState.MISSING,
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
                tracker_title=row.title,
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
            UpgradeCandidate.execution_state.in_(["submitted", "linking", "downloading", "validating"]),
        )
    )
    if row is None:
        return
    if row.execution_state == "linking":
        from app.services.same_file_links import release_linking_row

        release_linking_row(row, "The torrent was removed from qBittorrent before its files were checked.")
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


def _finishes_like_upgrade(row: UpgradeCandidate) -> bool:
    """A replacement for a trumped title retires and cleans up automatically."""

    from app.services.replacements import TRUMPED

    return row.target_key.startswith("replace:") and (row.comparison_snapshot or {}).get("replacement_reason") == TRUMPED


async def advance_upgrade_handoff(
    db: AsyncSession,
    torrent: Torrent,
    *,
    qbittorrent_client_factory: QBittorrentClientFactory,
    allow_torrent_removal: bool = False,
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
    from app.services.torrent_archive import torrent_archive_complete
    if torrent.archive_state != TorrentArchiveState.ARCHIVED or (row.target_key.startswith("replace:") and not torrent_archive_complete(torrent)):
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
        if row.target_key.startswith("replace:"):
            for item in old:
                if item.release_state == ReleaseState.MISSING:
                    item.release_state = ReleaseState.REPLACED
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
    if row.target_key.startswith("replace:"):
        for old_torrent, _observation, discovered_cross_seed in await _old_live_torrents(
            db, row, exclude_torrent_id=torrent.id
        ):
            old_torrent.metadata_json = {
                **old_torrent.metadata_json,
                "replacement_candidate_id": str(row.id),
                "cross_seed_discovered_by_path": discovered_cross_seed,
            }
    if row.target_key.startswith("replace:") and not allow_torrent_removal and not _finishes_like_upgrade(row):
        row.execution_state = "cleanup_required"
        row.execution_error = None
        return row
    live_old = await _old_live_torrents(db, row, exclude_torrent_id=torrent.id)
    for old_torrent, _observation, _discovered_cross_seed in live_old:
        if old_torrent.archive_state != TorrentArchiveState.ARCHIVED:
            row.execution_state = "handoff_attention"
            row.execution_error = f"Old torrent {old_torrent.info_hash} is no longer safely archived, so it was kept in qBittorrent."
            return row

    failures: list[str] = []
    for old_torrent, observation, discovered_cross_seed in live_old:
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
                details={
                    "candidate_id": str(row.id),
                    "client_id": str(client.id),
                    "info_hash": old_torrent.info_hash,
                    "delete_files": False,
                    "cross_seed_discovered_by_path": discovered_cross_seed,
                },
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
        message="Replacement validated and old qBittorrent torrents removed without deleting their files.",
        details={"candidate_id": str(row.id), "torrent_id": str(torrent.id), "old_release_ids": list(row.current_release_ids or [])},
    )
    if bool((row.comparison_snapshot or {}).get("delete_old_media_after_upgrade")):
        core = await _cleanup_core(db, row)
        if core["blocked"]:
            await _block_automatic_cleanup(db, row, core["blocked"])
        else:
            # Files are deleted only by run_automatic_cleanups(), after this
            # validated handoff has been committed. Deleting inside the
            # caller's transaction could leave media gone while a later
            # rollback restores the old release as current.
            row.comparison_snapshot = {**(row.comparison_snapshot or {}), "automatic_cleanup_pending": True}
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


