"""Matching qBittorrent torrents to library titles: association, completion, disagreements and cancellation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.integration_config import get_integration_config_store
from app.integrations.filesystem import FilesystemObserver
from app.models.domain import (
    AssociationType,
    MediaDirectory,
    MediaFile,
    EpisodeMediaMap,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    Show,
    Season,
    ShowRelease,
    ShowReleaseTorrent,
    QualityDefinition,
    ReleaseScope,
    ReleaseState,
    StorageRoot,
    Torrent,
)
from app.parser import parse_release
from app.services.events import create_event
from app.services.quality_profiles import evaluate_current_release_score, preferred_release_name
from app.services.reconciliation import (
    _confidence,
    _find_movie_by_identity,
    _same_edition,
    open_problem,
    reconcile_movie_directory,
    resolve_problem,
    utcnow,
)


async def associate_incoming_torrent(
    db: AsyncSession,
    torrent: Torrent,
    *,
    resolved_path: str | None,
    scope: MediaType,
    complete: bool,
    download_client_id: UUID | None = None,
) -> dict[str, Any] | None:
    """Associate a confidently parsed in-scope torrent before completion."""

    if download_client_id is not None:
        from app.services.upgrade_execution import associate_upgrade_torrent

        upgrade = await associate_upgrade_torrent(
            db,
            torrent,
            download_client_id=download_client_id,
            scope=scope,
            complete=complete,
        )
        if upgrade is not None:
            return upgrade

    parsed = parse_release(torrent.name)

    if scope == MediaType.SHOWS:
        title = parsed.identity.title_candidate
        season_number = parsed.identity.season
        if not title or season_number is None:
            return None
        # ShowRelease has a single Season foreign key. A multi-season torrent
        # (S01-S04) cannot safely be represented as an incoming Season 1 pack;
        # its individual files will be associated when they exist and scan.
        if len(parsed.identity.seasons) > 1:
            return None
        statement = select(Show).where(func.lower(Show.title) == title.casefold())
        if parsed.identity.year is not None:
            statement = statement.where((Show.year == parsed.identity.year) | Show.year.is_(None))
        candidates = (await db.scalars(statement)).all()
        if len(candidates) != 1:
            return None
        show = candidates[0]
        season = await db.scalar(
            select(Season).where(Season.show_id == show.id, Season.season_number == season_number)
        )
        if season is None:
            season = Season(
                show_id=show.id,
                season_number=season_number,
                title=f"Season {season_number}",
                monitored=True,
                # Season 0 follows the library-wide Specials setting, the same
                # as a season created from TMDB metadata. Taking the column
                # default here made a scanned Specials folder count even with
                # the setting off.
                counted=season_number != 0 or get_integration_config_store().get_count_specials(),
                metadata_json={},
            )
            db.add(season)
            await db.flush()

        assoc = await db.scalar(
            select(ShowReleaseTorrent)
            .join(ShowRelease, ShowRelease.id == ShowReleaseTorrent.show_release_id)
            .where(ShowReleaseTorrent.torrent_id == torrent.id, ShowRelease.show_id == show.id)
        )
        if assoc is None:
            scope_value = (
                ReleaseScope.SEASON_PACK
                if not parsed.identity.episodes
                else ReleaseScope.MULTI_EPISODE
                if len(parsed.identity.episodes) > 1
                else ReleaseScope.EPISODE
            )
            quality = None
            if parsed.quality.canonical:
                quality = await db.scalar(
                    select(QualityDefinition).where(QualityDefinition.name == parsed.quality.canonical)
                )
            release = ShowRelease(
                show_id=show.id,
                season_id=season.id,
                raw_release_name=torrent.name,
                tracker_title=str((torrent.metadata_json or {}).get("tracker_title") or "").strip() or None,
                release_scope=scope_value,
                quality_definition_id=quality.id if quality else None,
                release_group=parsed.release_group,
                release_state=ReleaseState.MISSING,
                parse_snapshot={
                    **parsed.to_dict(),
                    "identity_confidence": 0.95,
                    "incoming": True,
                    "incoming_kind": "season_pack" if scope_value == ReleaseScope.SEASON_PACK else "release",
                },
            )
            db.add(release)
            await db.flush()
            current_score, snapshot = await evaluate_current_release_score(
                db,
                media_type=MediaType.SHOWS,
                entity_id=show.id,
                release_name=preferred_release_name(release),
                indexer=str((torrent.metadata_json or {}).get("indexer_name") or "").strip() or None,
            )
            release.current_custom_format_score = current_score
            release.parse_snapshot = {**release.parse_snapshot, "current_score_snapshot": snapshot}
            assoc = ShowReleaseTorrent(
                show_release_id=release.id,
                torrent_id=torrent.id,
                association_type=AssociationType.INCOMING,
            )
            db.add(assoc)
            await create_event(
                db,
                "torrent.incoming",
                entity_type="show",
                entity_id=show.id,
                message=(
                    f"Incoming season pack detected for {show.title} Season {season_number}."
                    if scope_value == ReleaseScope.SEASON_PACK
                    else f"Incoming release detected for {show.title}."
                ),
                details={
                    "torrent_id": str(torrent.id),
                    "release_id": str(release.id),
                    "progress": torrent.metadata_json.get("progress"),
                    "release_scope": scope_value.value,
                    "season_number": season_number,
                    "episode_numbers": list(parsed.identity.episodes),
                },
            )
        elif assoc.association_type != AssociationType.ATTACHED and not complete:
            assoc.association_type = AssociationType.INCOMING
        return {
            "show_id": show.id,
            "release_id": assoc.show_release_id,
            "confidence": 0.95,
            "resolved_path": resolved_path,
        }

    if scope != MediaType.MOVIES:
        return None
    title = parsed.identity.title_candidate
    year = parsed.identity.year
    if not title or year is None:
        return None
    movie = await _find_movie_by_identity(db, title, year)
    if movie is None:
        return None
    confidence = 0.95
    assoc = await db.scalar(
        select(MovieReleaseTorrent)
        .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
        .where(MovieReleaseTorrent.torrent_id == torrent.id, MovieRelease.movie_id == movie.id)
    )
    if assoc is None:
        existing = (
            await db.scalars(
                select(MovieRelease)
                .options(selectinload(MovieRelease.directories))
                .where(MovieRelease.movie_id == movie.id)
            )
        ).all()
        missing = [
            item
            for item in existing
            if item.release_state in {ReleaseState.CURRENT, ReleaseState.MISSING}
            and not any(directory.exists for directory in item.directories)
        ]
        replacement_target = None
        same_edition = [item for item in missing if _same_edition(item.effective_edition, parsed.edition)]
        if len(same_edition) == 1:
            replacement_target = same_edition[0]
        elif len(missing) == 1:
            replacement_target = missing[0]
        incoming_kind = "replacement" if replacement_target is not None else "release"
        release = MovieRelease(
            movie_id=movie.id,
            raw_release_name=torrent.name,
            parsed_title=title,
            parsed_year=year,
            parsed_edition=parsed.edition,
            effective_edition=parsed.edition,
            release_state=ReleaseState.MISSING,
            parse_snapshot={
                **parsed.to_dict(),
                "identity_confidence": confidence,
                "incoming": True,
                "incoming_kind": incoming_kind,
                "replacement_of_release_id": str(replacement_target.id) if replacement_target else None,
            },
        )
        db.add(release)
        await db.flush()
        assoc = MovieReleaseTorrent(
            movie_release_id=release.id,
            torrent_id=torrent.id,
            association_type=AssociationType.INCOMING,
        )
        db.add(assoc)
        await create_event(
            db,
            "torrent.incoming",
            entity_type="movie",
            entity_id=movie.id,
            message=(
                f"Incoming replacement detected for {movie.title}."
                if replacement_target is not None
                else f"Incoming release detected for {movie.title}."
            ),
            details={
                "torrent_id": str(torrent.id),
                "release_id": str(release.id),
                "progress": torrent.metadata_json.get("progress"),
                "incoming_kind": incoming_kind,
                "replacement_of_release_id": str(replacement_target.id) if replacement_target else None,
            },
        )
    elif assoc.association_type != AssociationType.ATTACHED and not complete:
        assoc.association_type = AssociationType.INCOMING
    return {"movie_id": movie.id, "release_id": assoc.movie_release_id, "confidence": confidence, "resolved_path": resolved_path}


async def finalize_completed_torrent(
    db: AsyncSession,
    torrent: Torrent,
    *,
    resolved_path: str | None,
    scope: MediaType,
) -> str:
    """Verify and attach a completed torrent, preserving old evidence."""

    if scope == MediaType.SHOWS:
        return await _finalize_completed_show_torrent(db, torrent, resolved_path=resolved_path)

    if scope != MediaType.MOVIES or not resolved_path:
        await open_problem(
            db,
            reason="TORRENT_PATH_NOT_FOUND",
            entity_type="torrent",
            entity_id=torrent.id,
            message="Completed torrent has no resolved content path.",
            details={"torrent_id": str(torrent.id), "path": resolved_path},
        )
        return "problem"
    path = Path(resolved_path)
    # Prefix-safe root lookup is done in Python so `/movies-evil` never
    # matches `/movies`.  This query also works for mapped nested paths.
    roots = (await db.scalars(select(StorageRoot).where(
        StorageRoot.enabled.is_(True),
        StorageRoot.last_scan_at.is_not(None),
        StorageRoot.media_type == MediaType.MOVIES,
    ))).all()
    root = next((item for item in roots if _inside_root(str(path), item.resolved_root_path)), None)
    if root is None:
        # qBittorrent may manage many libraries that are intentionally outside
        # Medialogue's configured media folders. Such a path is out of scope,
        # not missing.  Never manufacture a filesystem-access Problem for it.
        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
        return "ignored"
    if not Path(root.resolved_root_path).is_dir():
        return "deferred"
    if not path.exists():
        await open_problem(
            db,
            reason="TORRENT_PATH_NOT_FOUND",
            entity_type="torrent",
            entity_id=torrent.id,
            message="qBittorrent reports a completed path that is not accessible.",
            details={"torrent_id": str(torrent.id), "resolved_path": resolved_path, "root_id": str(root.id) if root else None},
        )
        return "problem"
    if path.is_file():
        path = path.parent
    try:
        observation = FilesystemObserver().inspect_directory(path, Path(root.resolved_root_path))
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        await open_problem(
            db,
            reason="TORRENT_PATH_NOT_FOUND",
            entity_type="torrent",
            entity_id=torrent.id,
            message="Could not inspect completed torrent directory.",
            details={"resolved_path": str(path), "error": str(exc)},
        )
        return "problem"
    await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
    parsed = parse_release(observation.name)
    files = [parse_release(Path(name).stem) for name in observation.media_files]
    confidence = _confidence(parsed, files, has_disc_structure=observation.has_dvd_structure or observation.has_bluray_structure)
    assoc = await db.scalar(
        select(MovieReleaseTorrent)
        .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
        .where(MovieReleaseTorrent.torrent_id == torrent.id, MovieReleaseTorrent.association_type == AssociationType.INCOMING)
        .order_by(MovieReleaseTorrent.created_at.desc())
    )
    movie = None
    incoming_release = None
    if assoc is not None:
        linked_release = await db.get(MovieRelease, assoc.movie_release_id)
        if linked_release is not None:
            movie = await db.get(Movie, linked_release.movie_id)
            incoming_release = linked_release
    if movie is None and parsed.identity.title_candidate and parsed.identity.year is not None:
        movie = await _find_movie_by_identity(db, parsed.identity.title_candidate, parsed.identity.year)
    # Plex metadata never blocks a TMDB-backed movie attachment. Presence/path
    # evidence is retained, while pending/unavailable remain non-authoritative.
    result = await reconcile_movie_directory(
        db,
        root,
        observation,
        torrent=torrent,
        movie_hint=movie,
        incoming_release=incoming_release,
        force_new_media=confidence < 0.90,
    )
    if result in {"matched", "duplicates", "conflicts"}:
        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
    return result


async def _finalize_completed_show_torrent(
    db: AsyncSession,
    torrent: Torrent,
    *,
    resolved_path: str | None,
) -> str:
    if not resolved_path:
        await open_problem(
            db,
            reason="TORRENT_PATH_NOT_FOUND",
            entity_type="torrent",
            entity_id=torrent.id,
            message="Completed Show torrent has no resolved content path.",
            details={"torrent_id": str(torrent.id), "path": resolved_path},
        )
        return "problem"

    path = Path(resolved_path)
    roots = (
        await db.scalars(
            select(StorageRoot).where(
                StorageRoot.enabled.is_(True),
                StorageRoot.last_scan_at.is_not(None),
                StorageRoot.media_type == MediaType.SHOWS,
            )
        )
    ).all()
    root = next((item for item in roots if _inside_root(str(path), item.resolved_root_path)), None)
    if root is None:
        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
        return "ignored"
    if not Path(root.resolved_root_path).is_dir():
        return "deferred"
    if not path.exists():
        await open_problem(
            db,
            reason="TORRENT_PATH_NOT_FOUND",
            entity_type="torrent",
            entity_id=torrent.id,
            message="qBittorrent reports a completed Show path that is not accessible.",
            details={"torrent_id": str(torrent.id), "resolved_path": resolved_path, "root_id": str(root.id) if root else None},
        )
        return "problem"

    root_path = Path(root.resolved_root_path).resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        relative = resolved.relative_to(root_path)
    except ValueError:
        relative = None
    if relative is None or not relative.parts:
        await open_problem(
            db,
            reason="TORRENT_PATH_NOT_FOUND",
            entity_type="torrent",
            entity_id=torrent.id,
            message="Completed Show torrent does not resolve to a Show container beneath the configured root.",
            details={"resolved_path": str(path), "root": str(root_path)},
        )
        return "problem"

    container = root_path / relative.parts[0]
    if not container.is_dir():
        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
        return "ignored"
    try:
        observation = FilesystemObserver().inspect_directory(container, root_path)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        await open_problem(
            db,
            reason="TORRENT_PATH_NOT_FOUND",
            entity_type="torrent",
            entity_id=torrent.id,
            message="Could not inspect completed Show torrent directory.",
            details={"resolved_path": str(container), "error": str(exc)},
        )
        return "problem"

    assoc = await db.scalar(
        select(ShowReleaseTorrent)
        .join(ShowRelease, ShowRelease.id == ShowReleaseTorrent.show_release_id)
        .where(
            ShowReleaseTorrent.torrent_id == torrent.id,
            ShowReleaseTorrent.association_type == AssociationType.INCOMING,
        )
        .order_by(ShowReleaseTorrent.created_at.desc())
    )
    incoming_release = None
    show = None
    if assoc is not None:
        incoming_release = await db.get(ShowRelease, assoc.show_release_id)
        if incoming_release is not None:
            show = await db.get(Show, incoming_release.show_id)

    if show is None:
        parsed = parse_release(torrent.name)
        if parsed.identity.title_candidate:
            statement = select(Show).where(func.lower(Show.title) == parsed.identity.title_candidate.casefold())
            candidates = (await db.scalars(statement)).all()
            if len(candidates) == 1:
                show = candidates[0]

    member_paths: set[str] = set()
    if path.is_file():
        try:
            member_paths.add(path.resolve(strict=False).relative_to(container).as_posix())
        except ValueError:
            pass
    else:
        try:
            member_prefix = path.resolve(strict=False).relative_to(container)
        except ValueError:
            member_prefix = Path('.')
        for relative_name in observation.media_files:
            relative_media = Path(relative_name)
            if str(member_prefix) in {'.', ''}:
                member_paths.add(relative_name)
            else:
                try:
                    relative_media.relative_to(member_prefix)
                    member_paths.add(relative_name)
                except ValueError:
                    pass

    from app.services.shows import reconcile_show_directory

    result = await reconcile_show_directory(
        db,
        root,
        observation,
        torrent=torrent,
        show_hint=show,
        incoming_release=incoming_release,
        torrent_member_paths=member_paths,
    )
    if result in {"matched", "review"}:
        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
    return result


async def reconcile_torrent_disagreements(
    db: AsyncSession,
    torrent: Torrent,
    *,
    qbit_present: bool | None,
) -> None:
    """Retire torrent links whose mapped media no longer exists.

    Removing a torrent while retaining its media is normal and does not create
    a Problem. ``qbit_present`` remains part of the observer contract but is no
    longer treated as library disagreement.
    """

    movie_links = (await db.scalars(select(MovieReleaseTorrent).where(MovieReleaseTorrent.torrent_id == torrent.id))).all()
    for link in movie_links:
        if link.association_type == AssociationType.INCOMING:
            continue
        release = await db.get(MovieRelease, link.movie_release_id)
        if release is None:
            continue
        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "movie_release", release.id)
        if link.association_type == AssociationType.HISTORICAL:
            continue
        directories = (await db.scalars(select(MediaDirectory).where(MediaDirectory.movie_release_id == release.id))).all()
        media_present = any(directory.exists for directory in directories)
        if not media_present:
            # The filesystem is authoritative for established library media.
            # A qBittorrent-only remnant is historical evidence, not an
            # actionable library fault. Active/incoming completion failures are
            # handled earlier by finalize_completed_torrent on the Torrent.
            link.association_type = AssociationType.HISTORICAL
        continue

    show_links = (await db.scalars(select(ShowReleaseTorrent).where(ShowReleaseTorrent.torrent_id == torrent.id))).all()
    for link in show_links:
        if link.association_type == AssociationType.INCOMING:
            continue
        release = await db.get(ShowRelease, link.show_release_id)
        if release is None:
            continue
        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "show_release", release.id)
        if link.association_type == AssociationType.HISTORICAL:
            continue
        media_present = bool(
            await db.scalar(
                select(func.count())
                .select_from(EpisodeMediaMap)
                .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
                .where(EpisodeMediaMap.show_release_id == release.id, MediaFile.exists.is_(True))
            )
        )
        if not media_present:
            link.association_type = AssociationType.HISTORICAL


async def cancel_incoming_torrent(
    db: AsyncSession,
    torrent: Torrent,
    *,
    emit_event: bool = True,
) -> int:
    """Deactivate provisional Incoming links.

    ``emit_event`` is disabled when a still-present torrent merely leaves the
    configured Medialogue storage scope; that is not a qBittorrent removal.
    """

    movie_links = (
        await db.scalars(
            select(MovieReleaseTorrent).where(
                MovieReleaseTorrent.torrent_id == torrent.id,
                MovieReleaseTorrent.association_type == AssociationType.INCOMING,
            )
        )
    ).all()
    cancelled = 0
    for link in movie_links:
        link.association_type = AssociationType.HISTORICAL
        release = await db.get(MovieRelease, link.movie_release_id)
        has_directory = bool(
            await db.scalar(
                select(func.count()).select_from(MediaDirectory).where(MediaDirectory.movie_release_id == link.movie_release_id)
            )
        )
        if release is not None and not has_directory:
            release.release_state = ReleaseState.REMOVED
            release.removed_at = utcnow()
        cancelled += 1

    show_links = (
        await db.scalars(
            select(ShowReleaseTorrent).where(
                ShowReleaseTorrent.torrent_id == torrent.id,
                ShowReleaseTorrent.association_type == AssociationType.INCOMING,
            )
        )
    ).all()
    for link in show_links:
        link.association_type = AssociationType.HISTORICAL
        release = await db.get(ShowRelease, link.show_release_id)
        has_media = bool(
            await db.scalar(
                select(func.count()).select_from(EpisodeMediaMap).where(EpisodeMediaMap.show_release_id == link.show_release_id)
            )
        )
        if release is not None and not has_media:
            release.release_state = ReleaseState.REMOVED
        cancelled += 1

    if cancelled:
        from app.services.upgrade_execution import mark_upgrade_torrent_removed

        await mark_upgrade_torrent_removed(db, torrent)

    if cancelled and emit_event:
        await create_event(
            db,
            "download.cancelled",
            entity_type="torrent",
            entity_id=torrent.id,
            message="Incoming download was removed before completion.",
            details={"torrent_id": str(torrent.id), "cancelled_associations": cancelled},
        )
    return cancelled


def _inside_root(path: str, root: str) -> bool:
    try:
        Path(path).resolve(strict=False).relative_to(Path(root).resolve(strict=False))
        return True
    except ValueError:
        return False
