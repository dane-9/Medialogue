from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.integrations.filesystem import DirectoryObservation
from app.models.domain import (
    Episode,
    EpisodeMediaMap,
    IdentityState,
    MatchMethod,
    AssociationType,
    MediaDirectory,
    MediaFile,
    MediaRole,
    MediaType,
    ParseEvidence,
    PlexMatchState,
    PlexObservation,
    PresenceState,
    Problem,
    ProblemStatus,
    QualityDefinition,
    ReleaseScope,
    ReleaseState,
    Season,
    Severity,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    Torrent,
    SourceType,
    StorageRoot,
)
from app.parser import extract_episode_numbers, parse_release_name, parse_season_folder
from app.services.events import create_event
from app.services.quality_profiles import evaluate_current_release_score
from app.services.reconciliation import open_problem, resolve_problem
from app.services.tmdb import TMDBUnavailable, resolve_show_identity, sync_show_metadata


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def resolve_show_resource(db: AsyncSession, resource_id: str) -> Show | None:
    if resource_id.isdigit():
        return await db.scalar(select(Show).where(Show.tmdb_id == int(resource_id)))
    try:
        show_id = UUID(resource_id)
    except ValueError:
        return None
    return await db.get(Show, show_id)


async def add_show_from_tmdb(
    db: AsyncSession,
    tmdb_id: int,
    *,
    monitored: bool = True,
    client_factory=None,
) -> Show:
    existing = await db.scalar(select(Show).where(Show.tmdb_id == tmdb_id))
    if existing is not None:
        return existing
    # Create a provisional row so the metadata sync can populate all fields in
    # one place. If TMDB cannot return details, abort rather than inventing a
    # title for a logical Show.
    show = Show(
        title=f"TMDB {tmdb_id}",
        year=None,
        tmdb_id=tmdb_id,
        monitored=monitored,
        identity_state=IdentityState.MATCHED,
    )
    db.add(show)
    await db.flush()
    try:
        result = await sync_show_metadata(db, show, client_factory=client_factory)
    except Exception:
        await db.delete(show)
        await db.flush()
        raise
    if result["seasons"] == 0 and show.title == f"TMDB {tmdb_id}":
        await db.delete(show)
        await db.flush()
        raise RuntimeError("TMDB did not return show metadata")
    await create_event(
        db,
        "show.added",
        entity_type="show",
        entity_id=show.id,
        message=f"Added {show.title} from TMDB.",
        details={"tmdb_id": tmdb_id},
    )
    return show


async def _ensure_season(db: AsyncSession, show: Show, season_number: int) -> Season:
    season = await db.scalar(
        select(Season).where(Season.show_id == show.id, Season.season_number == season_number)
    )
    if season is None:
        season = Season(
            show_id=show.id,
            season_number=season_number,
            title=f"Season {season_number}",
            monitored=True,
            metadata_json={},
        )
        db.add(season)
        await db.flush()
    return season


async def _ensure_episode_for_mapping(
    db: AsyncSession,
    show: Show,
    season: Season,
    episode_number: int,
    *,
    title_hint: str | None = None,
) -> Episode | None:
    episode = await db.scalar(
        select(Episode).where(
            Episode.show_id == show.id,
            Episode.season_number == season.season_number,
            Episode.episode_number == episode_number,
        )
    )
    if episode is not None:
        return episode

    # Once TMDB has fully populated a season, a number outside that inventory
    # is not safe to invent. If metadata is incomplete/unavailable, retain the
    # leave-in-place inventory behavior and create a local placeholder instead.
    expected = int((season.metadata_json or {}).get("episode_count") or 0)
    known = int(
        await db.scalar(select(func.count()).select_from(Episode).where(Episode.season_id == season.id)) or 0
    )
    if expected > 0 and known >= expected:
        return None

    episode = Episode(
        show_id=show.id,
        season_id=season.id,
        season_number=season.season_number,
        episode_number=episode_number,
        title=title_hint,
        monitored=True,
        presence_state=PresenceState.MISSING,
        metadata_json={"provider": "local_observation"},
    )
    db.add(episode)
    await db.flush()
    return episode


async def _quality_for_parse(db: AsyncSession, parsed: Any) -> QualityDefinition | None:
    if not parsed.quality.canonical:
        return None
    return await db.scalar(select(QualityDefinition).where(QualityDefinition.name == parsed.quality.canonical))


async def _prepare_show_release(
    db: AsyncSession,
    *,
    show: Show,
    season: Season,
    raw_release_name: str,
    scope: ReleaseScope,
    parsed: Any,
    existing: ShowRelease | None = None,
) -> ShowRelease:
    quality = await _quality_for_parse(db, parsed)
    release = existing
    if release is None:
        release = ShowRelease(
            show_id=show.id,
            season_id=season.id,
            raw_release_name=raw_release_name,
            release_scope=scope,
            quality_definition_id=quality.id if quality else None,
            release_group=parsed.release_group,
            release_state=ReleaseState.CURRENT,
            parse_snapshot={**parsed.to_dict(), "identity_confidence": 1.0},
        )
        db.add(release)
        await db.flush()
    else:
        release.show_id = show.id
        release.season_id = season.id
        release.release_scope = scope
        release.release_state = ReleaseState.CURRENT
        release.quality_definition_id = quality.id if quality else release.quality_definition_id
        release.release_group = parsed.release_group or release.release_group
        release.parse_snapshot = {
            **dict(release.parse_snapshot or {}),
            **parsed.to_dict(),
            "identity_confidence": 1.0,
            "incoming": False,
        }

    current_score, snapshot = await evaluate_current_release_score(
        db,
        media_type=MediaType.SHOWS,
        entity_id=show.id,
        release_name=release.raw_release_name,
    )
    release.current_custom_format_score = current_score
    release.parse_snapshot = {**dict(release.parse_snapshot or {}), "current_score_snapshot": snapshot}
    return release


async def _refresh_episode_duplicate_problem(db: AsyncSession, episode: Episode) -> None:
    rows = (
        await db.execute(
            select(EpisodeMediaMap, MediaFile, MediaDirectory, ShowRelease)
            .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
            .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
            .outerjoin(ShowRelease, ShowRelease.id == EpisodeMediaMap.show_release_id)
            .where(EpisodeMediaMap.episode_id == episode.id)
        )
    ).all()

    # Catalogue presence intentionally survives the missing grace window. It is
    # therefore not, by itself, proof that two files are on disk: after a rename
    # the stale row and the newly observed row would otherwise manufacture a
    # duplicate. Canonical paths also collapse duplicate directory records that
    # happen to describe the same physical file.
    evidence: list[dict[str, Any]] = []
    verified_by_path: dict[str, dict[str, Any]] = {}
    for mapping, media_file, directory, release in rows:
        path = Path(directory.resolved_path) / media_file.relative_path
        canonical_path = str(path.resolve(strict=False))
        physically_present = bool(
            media_file.exists
            and directory.exists
            and media_file.missing_since is None
            and directory.missing_since is None
            and path.is_file()
        )
        item = {
            "media_file_id": str(media_file.id),
            "filename": media_file.filename,
            "path": str(path),
            "canonical_path": canonical_path,
            "catalogue_exists": bool(media_file.exists),
            "physical_exists": physically_present,
            "missing_since": media_file.missing_since.isoformat() if media_file.missing_since else None,
            "show_release_id": str(mapping.show_release_id) if mapping.show_release_id else None,
            "release_name": release.raw_release_name if release is not None else None,
            "manual_mapping": bool(mapping.manual_override),
        }
        evidence.append(item)
        if physically_present:
            verified_by_path.setdefault(canonical_path, item)

    verified = list(verified_by_path.values())
    if len(verified) > 1:
        existing_problem = await db.scalar(
            select(Problem).where(
                Problem.reason == "DUPLICATE_EPISODE_RELEASE",
                Problem.entity_type == "episode",
                Problem.entity_id == episode.id,
                Problem.status == ProblemStatus.OPEN,
            )
        )
        preferred_id = str((existing_problem.details or {}).get("preferred_media_file_id") or "") if existing_problem else ""
        verified_ids = {item["media_file_id"] for item in verified}
        details: dict[str, Any] = {
            "media_file_ids": [item["media_file_id"] for item in verified],
            "media_files": evidence,
            "verified_physical_file_count": len(verified),
        }
        if preferred_id in verified_ids:
            details["preferred_media_file_id"] = preferred_id
        await open_problem(
            db,
            reason="DUPLICATE_EPISODE_RELEASE",
            entity_type="episode",
            entity_id=episode.id,
            message=f"S{episode.season_number:02d}E{episode.episode_number:02d} has multiple physical media files.",
            details=details,
            severity=Severity.WARNING,
        )
    else:
        await resolve_problem(db, "DUPLICATE_EPISODE_RELEASE", "episode", episode.id)


async def _refresh_episode_presence(db: AsyncSession, episode: Episode, *, emit_event: bool = True) -> None:
    existing = int(
        await db.scalar(
            select(func.count())
            .select_from(EpisodeMediaMap)
            .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
            .where(EpisodeMediaMap.episode_id == episode.id, MediaFile.exists.is_(True))
        )
        or 0
    )
    next_state = PresenceState.PRESENT if existing else PresenceState.MISSING
    if episode.presence_state != next_state:
        episode.presence_state = next_state
        if emit_event:
            await create_event(
                db,
                "episode.present" if next_state == PresenceState.PRESENT else "episode.missing",
                entity_type="episode",
                entity_id=episode.id,
                message=(
                    f"S{episode.season_number:02d}E{episode.episode_number:02d} is present."
                    if next_state == PresenceState.PRESENT
                    else f"S{episode.season_number:02d}E{episode.episode_number:02d} is missing."
                ),
                severity=Severity.WARNING if next_state == PresenceState.MISSING else Severity.INFO,
                details={"show_id": str(episode.show_id)},
            )
    await _refresh_episode_duplicate_problem(db, episode)



def _season_from_relative_path(relative_path: str) -> int | None:
    """Season number implied by the folders a file sits in, nearest first.

    Libraries organise episodes under Season 1 / Season.1 / S01 / S1940 and then
    often name the file with only an index (``01 - Pilot``). The folder is
    reliable evidence, so it stands in for a season the filename omits.
    """

    for part in reversed(Path(relative_path).parent.parts):
        season = parse_season_folder(part)
        if season is not None:
            return season
    return None


async def reconcile_show_directory(
    db: AsyncSession,
    root: StorageRoot,
    observation: DirectoryObservation,
    *,
    torrent: Torrent | None = None,
    show_hint: Show | None = None,
    incoming_release: ShowRelease | None = None,
    torrent_member_paths: set[str] | None = None,
) -> str:
    """Map episode, multi-episode, and season-pack media in place.

    A season pack is represented by one ShowRelease that may map to many
    MediaFiles/Episodes. A multi-episode file maps one MediaFile to every
    confidently identifiable Episode. Ambiguous members are isolated as
    Problems while the rest of the pack remains usable.
    """

    folder_parse = parse_release_name(observation.name)
    torrent_parse = parse_release_name(torrent.name) if torrent is not None else None
    identity_parse = torrent_parse or folder_parse
    title = identity_parse.identity.title_candidate or folder_parse.identity.title_candidate or observation.name
    year = identity_parse.identity.year or folder_parse.identity.year

    directory = await db.scalar(
        select(MediaDirectory)
        .options(selectinload(MediaDirectory.files).selectinload(MediaFile.episode_maps))
        .where(
            MediaDirectory.resolved_path == observation.path,
            (MediaDirectory.storage_root_id == root.id) | MediaDirectory.storage_root_id.is_(None),
        )
    )
    if directory is not None and directory.storage_root_id is None:
        # Reattach durable inventory retained after a configured root was
        # removed, rather than duplicating the same physical path.
        directory.storage_root_id = root.id
    if directory is None:
        directory = MediaDirectory(
            storage_root_id=root.id,
            reported_path=observation.path,
            resolved_path=observation.path,
            exists=True,
            source_type=SourceType.FILESYSTEM,
            files=[],
        )
        db.add(directory)
        await db.flush()
    directory.exists = True
    directory.last_seen_at = utcnow()
    directory.last_exists_check_at = utcnow()
    directory.missing_since = None
    directory.missing_check_count = 0

    show: Show | None = show_hint
    if show is None:
        # A previously mapped directory carries its Show identity through its
        # MediaFile -> Episode maps even when it contains several releases.
        for media_file in directory.files:
            for mapping in media_file.episode_maps:
                episode = await db.get(Episode, mapping.episode_id)
                if episode is not None:
                    show = await db.get(Show, episode.show_id)
                    break
            if show is not None:
                break

    if show is None:
        match, reason = await resolve_show_identity(db, title, year)
        if match is None:
            # See reconcile_movie_directory: an absent or unreachable TMDB is a
            # global state. Scans are gated on it being configured, so this only
            # fires on a mid-run outage and fails the job once.
            if reason in {"not_configured", "unavailable"}:
                raise TMDBUnavailable(
                    f"TMDB is {reason.replace('_', ' ')}; Show identity cannot be established. "
                    "Check Settings -> Metadata, then run the scan again."
                )
            await open_problem(
                db,
                reason="TMDB_SHOW_IDENTITY_UNRESOLVED",
                entity_type="media_directory",
                entity_id=directory.id,
                message=f"TMDB could not uniquely identify {title or 'this Show candidate'}.",
                details={"path": observation.path, "title": title, "year": year, "tmdb_reason": reason},
                severity=Severity.WARNING,
            )
            return "review"
        await resolve_problem(db, "TMDB_SHOW_IDENTITY_UNRESOLVED", "media_directory", directory.id)
        show = await db.scalar(select(Show).where(Show.tmdb_id == match.tmdb_id))
        if show is None:
            show = Show(
                title=match.title,
                year=match.year or year,
                tmdb_id=match.tmdb_id,
                overview=match.overview,
                poster_ref=match.poster_path,
                monitored=True,
                identity_state=IdentityState.MATCHED,
                metadata_refreshed_at=utcnow(),
            )
            db.add(show)
            await db.flush()
            try:
                await sync_show_metadata(db, show)
                await resolve_problem(db, "TMDB_SHOW_METADATA_UNAVAILABLE", "show", show.id)
            except Exception:
                await open_problem(
                    db,
                    reason="TMDB_SHOW_METADATA_UNAVAILABLE",
                    entity_type="show",
                    entity_id=show.id,
                    message="Show identity is confirmed, but TMDB episode metadata could not be refreshed.",
                    details={"tmdb_id": show.tmdb_id},
                )
            await create_event(
                db,
                "show.discovered",
                entity_type="show",
                entity_id=show.id,
                message=f"Discovered {show.title} from the configured Show root.",
                details={"tmdb_id": show.tmdb_id, "path": observation.path},
            )
    else:
        await resolve_problem(db, "TMDB_SHOW_IDENTITY_UNRESOLVED", "media_directory", directory.id)

    parsed_by_path: dict[str, Any] = {
        relative_path: parse_release_name(Path(relative_path).stem)
        for relative_path in observation.media_files
    }

    # Strong season-pack evidence comes from the torrent/folder release name.
    # For a filesystem-only discovery, require at least two episode files in
    # that same season so a generic Show folder is not collapsed into one pack.
    pack_parse = None
    for candidate in (torrent_parse, folder_parse):
        if candidate is not None and candidate.identity.season is not None and not candidate.identity.episodes:
            same_season_files = sum(
                1
                for item in parsed_by_path.values()
                if item.identity.season == candidate.identity.season and item.identity.episodes
            )
            if torrent is not None or same_season_files >= 2:
                pack_parse = candidate
                break

    pack_release: ShowRelease | None = None
    pack_season: Season | None = None
    pack_was_new = False
    pack_new_mappings = 0
    if pack_parse is not None:
        pack_season = await _ensure_season(db, show, pack_parse.identity.season)
        existing_pack = incoming_release if incoming_release and incoming_release.release_scope == ReleaseScope.SEASON_PACK else None
        if existing_pack is None and directory.show_release_id:
            candidate = await db.get(ShowRelease, directory.show_release_id)
            if candidate is not None and candidate.release_scope == ReleaseScope.SEASON_PACK and candidate.show_id == show.id:
                existing_pack = candidate
        pack_was_new = existing_pack is None
        pack_release = await _prepare_show_release(
            db,
            show=show,
            season=pack_season,
            raw_release_name=torrent.name if torrent is not None else observation.name,
            scope=ReleaseScope.SEASON_PACK,
            parsed=pack_parse,
            existing=existing_pack,
        )
        # Only bind the directory itself when it is actually the season-pack
        # container. Generic Show folders can contain several independent packs.
        if folder_parse.identity.season == pack_season.season_number and not folder_parse.identity.episodes:
            directory.show_release_id = pack_release.id
        if torrent is not None:
            link = await db.scalar(
                select(ShowReleaseTorrent).where(
                    ShowReleaseTorrent.show_release_id == pack_release.id,
                    ShowReleaseTorrent.torrent_id == torrent.id,
                )
            )
            if link is None:
                db.add(ShowReleaseTorrent(
                    show_release_id=pack_release.id,
                    torrent_id=torrent.id,
                    association_type=AssociationType.ATTACHED,
                ))
            else:
                link.association_type = AssociationType.ATTACHED

    seen_paths = set(observation.media_files)
    mapped = unresolved = 0
    touched_episode_ids: set[UUID] = set()
    touched_release_ids: set[UUID] = set()

    threshold = max(1, root.missing_grace_checks or 2)
    now = utcnow()
    for media_file in directory.files:
        media_file.last_exists_check_at = now
        if media_file.relative_path in seen_paths:
            if not media_file.exists:
                touched_episode_ids.update(mapping.episode_id for mapping in media_file.episode_maps)
            media_file.exists = True
            media_file.missing_since = None
            media_file.missing_check_count = 0
            continue
        if not media_file.exists:
            continue
        if media_file.missing_since is None:
            media_file.missing_since = now
            media_file.missing_check_count = 1
            continue
        media_file.missing_check_count = (media_file.missing_check_count or 0) + 1
        if media_file.missing_check_count < threshold:
            continue
        media_file.exists = False
        for mapping in media_file.episode_maps:
            touched_episode_ids.add(mapping.episode_id)
            if mapping.show_release_id:
                touched_release_ids.add(mapping.show_release_id)

    incoming_identity = None
    if incoming_release is not None:
        incoming_identity = (incoming_release.parse_snapshot or {}).get("identity") or {}

    for relative_path, parsed in parsed_by_path.items():
        media_file = next((item for item in directory.files if item.relative_path == relative_path), None)
        if media_file is None:
            media_file = MediaFile(
                media_directory_id=directory.id,
                relative_path=relative_path,
                filename=Path(relative_path).name,
                media_role=MediaRole.MULTI_EPISODE_VIDEO if len(parsed.identity.episodes) > 1 else MediaRole.EPISODE_VIDEO,
                exists=True,
                episode_maps=[],
            )
            db.add(media_file)
            await db.flush()
            directory.files.append(media_file)
        else:
            media_file.exists = True
            media_file.last_seen_at = utcnow()
            media_file.last_exists_check_at = utcnow()
            media_file.missing_since = None
            media_file.missing_check_count = 0
            media_file.media_role = MediaRole.MULTI_EPISODE_VIDEO if len(parsed.identity.episodes) > 1 else MediaRole.EPISODE_VIDEO

        db.add(ParseEvidence(
            source_type=SourceType.FILENAME,
            source_id=media_file.id,
            raw_name=Path(relative_path).stem,
            parse_snapshot=parsed.to_dict(),
            parser_version=parsed.parser_version,
        ))

        # A manual mapping is authoritative for the entire file, including
        # episode numbers intentionally removed from the parser-derived set.
        # If any current mapping is manual, a later scan may restore physical
        # presence but must not silently re-add parser mappings that the user
        # explicitly removed.
        manual_maps = [mapping for mapping in media_file.episode_maps if mapping.manual_override]
        if manual_maps:
            media_file.media_role = (
                MediaRole.MULTI_EPISODE_VIDEO if len(manual_maps) > 1 else MediaRole.EPISODE_VIDEO
            )
            for mapping in manual_maps:
                touched_episode_ids.add(mapping.episode_id)
                if mapping.show_release_id:
                    touched_release_ids.add(mapping.show_release_id)
                episode = await db.get(Episode, mapping.episode_id)
                if episode is not None:
                    episode.presence_state = PresenceState.PRESENT
            for reason in (
                "EPISODE_MAPPING_UNRESOLVED",
            ):
                await resolve_problem(db, reason, "media_file", media_file.id)
            mapped += 1
            continue

        season_number = parsed.identity.season
        episode_numbers = tuple(dict.fromkeys(parsed.identity.episodes))
        # An explicit S01E01 in the filename always wins. The season folder only
        # fills in what the filename left out.
        folder_season = _season_from_relative_path(relative_path)
        if season_number is None and folder_season is not None:
            season_number = folder_season
        if not episode_numbers and folder_season is not None:
            episode_numbers = extract_episode_numbers(Path(relative_path).stem)
        if season_number is None or not episode_numbers:
            unresolved += 1
            await open_problem(
                db,
                reason="EPISODE_MAPPING_UNRESOLVED",
                entity_type="media_file",
                entity_id=media_file.id,
                message="The file does not contain a confident season/episode mapping.",
                details={"path": str(Path(observation.path) / relative_path), "parse": parsed.to_dict()},
            )
            continue
        season = await _ensure_season(db, show, season_number)

        release: ShowRelease | None = None
        if (
            pack_release is not None
            and pack_season is not None
            and season_number == pack_season.season_number
            and (torrent_member_paths is None or relative_path in torrent_member_paths)
        ):
            release = pack_release
        else:
            existing_release_ids = {mapping.show_release_id for mapping in media_file.episode_maps if mapping.show_release_id}
            if len(existing_release_ids) == 1:
                release = await db.get(ShowRelease, next(iter(existing_release_ids)))
            if release is None and incoming_release is not None and incoming_identity:
                incoming_season = incoming_identity.get("season")
                incoming_episodes = tuple(incoming_identity.get("episodes") or ())
                if incoming_season == season_number and tuple(episode_numbers) == incoming_episodes:
                    release = incoming_release
            scope = ReleaseScope.MULTI_EPISODE if len(episode_numbers) > 1 else ReleaseScope.EPISODE
            release = await _prepare_show_release(
                db,
                show=show,
                season=season,
                raw_release_name=(torrent.name if torrent is not None and release is incoming_release else Path(relative_path).stem),
                scope=scope,
                parsed=(torrent_parse if torrent is not None and release is incoming_release and torrent_parse is not None else parsed),
                existing=release,
            )
            if torrent is not None and release is incoming_release:
                link = await db.scalar(
                    select(ShowReleaseTorrent).where(
                        ShowReleaseTorrent.show_release_id == release.id,
                        ShowReleaseTorrent.torrent_id == torrent.id,
                    )
                )
                if link is None:
                    db.add(ShowReleaseTorrent(
                        show_release_id=release.id,
                        torrent_id=torrent.id,
                        association_type=AssociationType.ATTACHED,
                    ))
                else:
                    link.association_type = AssociationType.ATTACHED
        touched_release_ids.add(release.id)

        unresolved_numbers: list[int] = []
        newly_mapped = 0
        for episode_number in episode_numbers:
            episode = await _ensure_episode_for_mapping(
                db,
                show,
                season,
                episode_number,
                title_hint=parsed.identity.episode_title if len(episode_numbers) == 1 else None,
            )
            if episode is None:
                unresolved_numbers.append(episode_number)
                continue
            touched_episode_ids.add(episode.id)
            mapping = await db.scalar(
                select(EpisodeMediaMap).where(
                    EpisodeMediaMap.episode_id == episode.id,
                    EpisodeMediaMap.media_file_id == media_file.id,
                )
            )
            if mapping is None:
                mapping = EpisodeMediaMap(
                    episode_id=episode.id,
                    media_file_id=media_file.id,
                    show_release_id=release.id,
                    match_method=MatchMethod.PARSER,
                    confidence=1.0,
                )
                db.add(mapping)
                newly_mapped += 1
                if release is pack_release:
                    pack_new_mappings += 1
                await create_event(
                    db,
                    "episode.present",
                    entity_type="episode",
                    entity_id=episode.id,
                    message=f"S{season_number:02d}E{episode_number:02d} is present.",
                    details={
                        "show_id": str(show.id),
                        "path": str(Path(observation.path) / relative_path),
                        "release_scope": release.release_scope.value,
                    },
                )
            else:
                # A manual correction has higher authority than a later parser
                # pass. Automatic reconciliation may restore presence but must
                # not rewrite its logical episode assignment.
                if not mapping.manual_override:
                    mapping.show_release_id = release.id
                    mapping.match_method = MatchMethod.PARSER
                    mapping.confidence = 1.0
            episode.presence_state = PresenceState.PRESENT

        if unresolved_numbers:
            unresolved += 1
            await open_problem(
                db,
                reason="EPISODE_MAPPING_UNRESOLVED",
                entity_type="media_file",
                entity_id=media_file.id,
                message="Some episode numbers in this file could not be verified against the known season.",
                details={
                    "path": str(Path(observation.path) / relative_path),
                    "mapped_episode_numbers": [item for item in episode_numbers if item not in unresolved_numbers],
                    "unresolved_episode_numbers": unresolved_numbers,
                },
            )
        else:
            await resolve_problem(db, "EPISODE_MAPPING_UNRESOLVED", "media_file", media_file.id)
        if newly_mapped or not unresolved_numbers:
            mapped += 1

    # Shared releases remain Current while any mapped member still exists.
    for release_id in touched_release_ids:
        release = await db.get(ShowRelease, release_id)
        if release is None:
            continue
        existing = int(
            await db.scalar(
                select(func.count())
                .select_from(EpisodeMediaMap)
                .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
                .where(EpisodeMediaMap.show_release_id == release.id, MediaFile.exists.is_(True))
            )
            or 0
        )
        release.release_state = ReleaseState.CURRENT if existing else ReleaseState.MISSING

    for episode_id in touched_episode_ids:
        episode = await db.get(Episode, episode_id)
        if episode is not None:
            await _refresh_episode_presence(db, episode)

    if pack_release is not None and pack_season is not None and (pack_was_new or pack_new_mappings):
        mapped_episode_count = int(
            await db.scalar(
                select(func.count(func.distinct(EpisodeMediaMap.episode_id))).where(EpisodeMediaMap.show_release_id == pack_release.id)
            )
            or 0
        )
        await create_event(
            db,
            "season_pack.mapped",
            entity_type="show_release",
            entity_id=pack_release.id,
            message=f"Mapped season pack for {show.title} Season {pack_season.season_number}.",
            details={
                "show_id": str(show.id),
                "season_number": pack_season.season_number,
                "episode_count": mapped_episode_count,
                "torrent_id": str(torrent.id) if torrent is not None else None,
            },
        )

    return "review" if unresolved and mapped == 0 else "matched"


async def set_media_file_episode_mappings(
    db: AsyncSession,
    media_file_id: UUID,
    episode_ids: list[UUID],
) -> dict[str, Any]:
    """Replace one file's logical episode mapping without touching the file."""

    media_file = await db.scalar(
        select(MediaFile)
        .options(selectinload(MediaFile.episode_maps), selectinload(MediaFile.media_directory))
        .where(MediaFile.id == media_file_id)
    )
    if media_file is None:
        raise LookupError("media file not found")
    unique_ids = list(dict.fromkeys(episode_ids))
    if not unique_ids:
        raise ValueError("at least one episode is required")
    episodes = (await db.scalars(select(Episode).where(Episode.id.in_(unique_ids)))).all()
    if len(episodes) != len(unique_ids):
        raise ValueError("one or more episodes do not exist")
    show_ids = {episode.show_id for episode in episodes}
    season_numbers = {episode.season_number for episode in episodes}
    if len(show_ids) != 1 or len(season_numbers) != 1:
        raise ValueError("all mapped episodes must belong to the same Show and Season")

    old_episode_ids = {mapping.episode_id for mapping in media_file.episode_maps}
    old_release_ids = {mapping.show_release_id for mapping in media_file.episode_maps if mapping.show_release_id}
    release = await db.get(ShowRelease, next(iter(old_release_ids))) if len(old_release_ids) == 1 else None
    show = await db.get(Show, next(iter(show_ids)))
    season = await db.get(Season, episodes[0].season_id)
    if show is None or season is None:
        raise ValueError("mapped Show/Season no longer exists")

    parsed = parse_release_name(Path(media_file.relative_path).stem)
    if release is None:
        release = await _prepare_show_release(
            db,
            show=show,
            season=season,
            raw_release_name=Path(media_file.relative_path).stem,
            scope=ReleaseScope.MULTI_EPISODE if len(unique_ids) > 1 else ReleaseScope.EPISODE,
            parsed=parsed,
        )
    elif release.release_scope != ReleaseScope.SEASON_PACK:
        release.release_scope = ReleaseScope.MULTI_EPISODE if len(unique_ids) > 1 else ReleaseScope.EPISODE

    for mapping in list(media_file.episode_maps):
        await db.delete(mapping)
    await db.flush()
    for episode in episodes:
        db.add(EpisodeMediaMap(
            episode_id=episode.id,
            media_file_id=media_file.id,
            show_release_id=release.id,
            match_method=MatchMethod.MANUAL,
            confidence=1.0,
            manual_override=True,
        ))
        episode.presence_state = PresenceState.PRESENT if media_file.exists else PresenceState.MISSING
    media_file.media_role = MediaRole.MULTI_EPISODE_VIDEO if len(unique_ids) > 1 else MediaRole.EPISODE_VIDEO
    await db.flush()

    for episode_id in old_episode_ids | set(unique_ids):
        episode = await db.get(Episode, episode_id)
        if episode is not None:
            await _refresh_episode_presence(db, episode)
    for reason in ("EPISODE_MAPPING_UNRESOLVED",):
        await resolve_problem(db, reason, "media_file", media_file.id)
    await create_event(
        db,
        "episode.mapping_corrected",
        entity_type="media_file",
        entity_id=media_file.id,
        message="Episode mapping was corrected manually without changing the media file.",
        details={
            "show_id": str(show.id),
            "season_number": season.season_number,
            "episode_ids": [str(item) for item in unique_ids],
            "episode_numbers": sorted(episode.episode_number for episode in episodes),
            "path": str(Path(media_file.media_directory.resolved_path) / media_file.relative_path) if getattr(media_file, "media_directory", None) else media_file.relative_path,
        },
    )
    return {
        "media_file_id": media_file.id,
        "show_release_id": release.id,
        "episode_ids": unique_ids,
        "episode_numbers": sorted(episode.episode_number for episode in episodes),
        "manual_override": True,
    }


async def mark_absent_show_directories(
    db: AsyncSession,
    root: StorageRoot,
    seen_paths: set[str],
    *,
    grace_checks: int | None = None,
) -> dict[str, int]:
    """Apply root-level Missing grace to Show containers and all mapped episodes."""

    threshold = max(1, grace_checks or root.missing_grace_checks or 3)
    directories = (
        await db.scalars(
            select(MediaDirectory)
            .options(selectinload(MediaDirectory.files).selectinload(MediaFile.episode_maps))
            .where(MediaDirectory.storage_root_id == root.id)
        )
    ).unique().all()
    now = utcnow()
    started = missing = restored = 0
    for directory in directories:
        directory.last_exists_check_at = now
        if directory.resolved_path in seen_paths:
            if not directory.exists:
                restored += 1
            directory.exists = True
            directory.last_seen_at = now
            directory.missing_since = None
            directory.missing_check_count = 0
            continue
        if not directory.exists:
            continue
        if directory.missing_since is None:
            directory.missing_since = now
            directory.missing_check_count = 1
            started += 1
            continue
        directory.missing_check_count = (directory.missing_check_count or 0) + 1
        if directory.missing_check_count < threshold:
            continue
        directory.exists = False
        missing += 1
        affected: set[UUID] = set()
        for media_file in directory.files:
            media_file.exists = False
            for mapping in media_file.episode_maps:
                affected.add(mapping.episode_id)
                if mapping.show_release_id:
                    release = await db.get(ShowRelease, mapping.show_release_id)
                    if release is not None:
                        release.release_state = ReleaseState.MISSING
        for episode_id in affected:
            episode = await db.get(Episode, episode_id)
            if episode is not None and episode.presence_state != PresenceState.MISSING:
                episode.presence_state = PresenceState.MISSING
                await create_event(
                    db,
                    "episode.missing",
                    entity_type="episode",
                    entity_id=episode.id,
                    message=f"S{episode.season_number:02d}E{episode.episode_number:02d} is missing.",
                    severity=Severity.WARNING,
                    details={"reason": "PATH_NOT_FOUND", "path": directory.resolved_path, "grace_checks": threshold},
                )
    return {"started": started, "missing": missing, "restored": restored, "threshold": threshold}


async def show_problem_count(db: AsyncSession, show_id: UUID) -> int:
    episode_ids = select(Episode.id).where(Episode.show_id == show_id)
    media_file_ids = (
        select(EpisodeMediaMap.media_file_id)
        .join(Episode, Episode.id == EpisodeMediaMap.episode_id)
        .where(Episode.show_id == show_id)
    )
    return int(
        await db.scalar(
            select(func.count()).select_from(Problem).where(
                Problem.status == ProblemStatus.OPEN,
                (
                    ((Problem.entity_type == "show") & (Problem.entity_id == show_id))
                    | ((Problem.entity_type == "episode") & Problem.entity_id.in_(episode_ids))
                    | ((Problem.entity_type == "media_file") & Problem.entity_id.in_(media_file_ids))
                ),
            )
        )
        or 0
    )


async def show_plex_state(db: AsyncSession, show_id: UUID) -> str:
    states = (
        await db.scalars(select(PlexObservation.match_state).where(PlexObservation.show_id == show_id))
    ).all()
    if not states:
        return "unknown"
    priority = {
        PlexMatchState.CONFLICT: 5,
        PlexMatchState.MULTIPLE_VERSIONS: 4,
        PlexMatchState.UNAVAILABLE: 3,
        PlexMatchState.PENDING: 2,
        PlexMatchState.NOT_FOUND: 1,
        PlexMatchState.MATCHED: 0,
    }
    return max(states, key=lambda state: priority.get(state, 0)).value
