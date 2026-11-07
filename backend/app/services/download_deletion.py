"""Delete a download from qBittorrent and its media from disk, from Downloads.

A plan comes first and is shown to the user: the folders that will be
deleted, every torrent that goes with them (cross-seeds of the same files
would be left broken, so they are removed too) and the library titles
affected. Executing re-plans from scratch and refuses anything the plan
would block, so nothing changed in between slips through.

What is deleted:
- a movie: the whole folder the movie is in, so files added by hand
  (subtitles, artwork) go too;
- a show: only the torrent's own season or episode content, never the show
  folder with its other seasons;
- a torrent not in the library: its own content.

Never deleted: a configured media folder itself, anything outside one, a
folder in a read-only media folder, or a folder that also holds another
title's media.

Afterwards the library either lists the title in Replace Media as Missing, so
a replacement is found, or forgets it ("forget"): a movie is removed from
Medialogue, a show loses those releases and stops wanting the season, and the
show goes only when nothing of it is left.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.models.domain import (AccessMode, EpisodeMediaMap, MediaDirectory, MediaFile, Movie, MovieRelease,
                               MovieReleaseTorrent, ReleaseState, Season, Severity, Show, ShowRelease,
                               ShowReleaseTorrent, StorageRoot, Torrent, TorrentClientObservation, UpgradeCandidate)
from app.services.events import create_event
from app.services.integration_state import get_configured_download_client
from app.services.torrent_tracking import deactivate_client_tracker_health

KEEP_IN_REPLACE_MEDIA = "replace"
FORGET = "forget"


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _inside(path: str, folder: str) -> bool:
    """`path` is `folder` or lies inside it."""

    path_key, folder_key = _key(path), _key(folder)
    return path_key == folder_key or path_key.startswith(folder_key.rstrip("\\/") + os.sep)


@dataclass
class PlannedTorrent:
    observation_id: UUID
    torrent_id: UUID
    client_id: UUID
    client_name: str
    info_hash: str
    name: str
    selected: bool


@dataclass
class PlannedTitle:
    kind: str  # "movie" or "show"
    id: UUID
    title: str
    year: int | None
    season_numbers: list[int] = field(default_factory=list)


@dataclass
class DeletionPlan:
    folders: list[str] = field(default_factory=list)
    torrents: list[PlannedTorrent] = field(default_factory=list)
    titles: list[PlannedTitle] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    movie_release_ids: set[UUID] = field(default_factory=set)
    show_release_ids: set[UUID] = field(default_factory=set)


async def plan_deletion(db: AsyncSession, observation_ids: list[UUID]) -> DeletionPlan:
    plan = DeletionPlan()
    selected = (await db.execute(
        select(TorrentClientObservation, Torrent)
        .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
        .where(TorrentClientObservation.id.in_(observation_ids), TorrentClientObservation.is_present.is_(True))
    )).all()
    if not selected:
        raise AppError("NOT_FOUND", "No live downloads matched the request.", status_code=404)
    roots = (await db.scalars(select(StorageRoot).where(StorageRoot.enabled.is_(True)))).all()

    folders: list[str] = []
    for observation, torrent in selected:
        movie_releases = (await db.scalars(
            select(MovieRelease).options(selectinload(MovieRelease.directories), selectinload(MovieRelease.movie))
            .join(MovieReleaseTorrent, MovieReleaseTorrent.movie_release_id == MovieRelease.id)
            .where(MovieReleaseTorrent.torrent_id == torrent.id)
        )).all()
        show_releases = (await db.scalars(
            select(ShowRelease).options(selectinload(ShowRelease.show), selectinload(ShowRelease.season))
            .join(ShowReleaseTorrent, ShowReleaseTorrent.show_release_id == ShowRelease.id)
            .where(ShowReleaseTorrent.torrent_id == torrent.id)
        )).all()
        movie_folders = [directory.resolved_path for release in movie_releases for directory in release.directories
                         if directory.resolved_path]
        if movie_folders:
            folders.extend(movie_folders)
        elif observation.resolved_save_path:
            folders.append(observation.resolved_save_path)
        else:
            plan.blocked.append(f"{torrent.name}: qBittorrent does not report where its files are.")
        for release in movie_releases:
            plan.movie_release_ids.add(release.id)
            if not any(item.kind == "movie" and item.id == release.movie_id for item in plan.titles):
                plan.titles.append(PlannedTitle("movie", release.movie_id, release.movie.title, release.movie.year))
        for release in show_releases:
            plan.show_release_ids.add(release.id)
            title = next((item for item in plan.titles if item.kind == "show" and item.id == release.show_id), None)
            if title is None:
                title = PlannedTitle("show", release.show_id, release.show.title, release.show.year)
                plan.titles.append(title)
            if release.season is not None and release.season.season_number not in title.season_numbers:
                title.season_numbers.append(release.season.season_number)

    # One folder inside another is deleted once, as the outer folder.
    for folder in sorted(set(folders), key=len):
        if not any(_inside(folder, kept) for kept in plan.folders):
            plan.folders.append(folder)

    for folder in plan.folders:
        root = next((item for item in roots if _inside(folder, item.resolved_root_path)), None)
        if root is None:
            plan.blocked.append(f"{folder} is not inside a configured media folder.")
        elif _key(folder) == _key(root.resolved_root_path):
            plan.blocked.append(f"{folder} is the {root.name} media folder itself.")
        elif root.access_mode != AccessMode.READ_WRITE:
            plan.blocked.append(f"{folder} is in {root.name}, which Medialogue may only read.")

    # Another title's media in the same folder would be deleted with it.
    others = (await db.scalars(
        select(MediaDirectory).options(selectinload(MediaDirectory.movie_release).selectinload(MovieRelease.movie),
                                       selectinload(MediaDirectory.show_release).selectinload(ShowRelease.show))
        .where(MediaDirectory.exists.is_(True))
        .where(or_(MediaDirectory.movie_release_id.is_not(None), MediaDirectory.show_release_id.is_not(None)))
    )).all()
    for folder in plan.folders:
        for directory in others:
            if not (_inside(directory.resolved_path, folder) or _inside(folder, directory.resolved_path)):
                continue
            if directory.movie_release_id and directory.movie_release_id not in plan.movie_release_ids and _inside(directory.resolved_path, folder):
                plan.blocked.append(f"{folder} also holds {directory.movie_release.movie.title}.")
            elif directory.show_release_id and directory.show_release_id not in plan.show_release_ids:
                files = (await db.scalars(select(MediaFile).where(MediaFile.media_directory_id == directory.id, MediaFile.exists.is_(True)))).all()
                if any(_inside(str(Path(directory.resolved_path) / item.relative_path), folder) for item in files):
                    plan.blocked.append(f"{folder} also holds other files of {directory.show_release.show.title}.")

    # Every torrent seeding from the deleted files goes with them.
    seen: set[UUID] = set()
    present = (await db.execute(
        select(TorrentClientObservation, Torrent)
        .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
        .where(TorrentClientObservation.is_present.is_(True))
    )).all()
    chosen = {observation.id for observation, _ in selected}
    for observation, torrent in present:
        inside = observation.id in chosen or (
            observation.resolved_save_path and any(_inside(observation.resolved_save_path, folder) for folder in plan.folders))
        if not inside or observation.id in seen:
            continue
        seen.add(observation.id)
        client = await get_configured_download_client(db, observation.download_client_id)
        plan.torrents.append(PlannedTorrent(observation.id, torrent.id, observation.download_client_id,
                                            client.name if client else "Unknown client", torrent.info_hash, torrent.name,
                                            observation.id in chosen))
    plan.blocked = list(dict.fromkeys(plan.blocked))
    return plan


def _delete_path(path: str) -> None:
    target = Path(path)
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


async def execute_deletion(db: AsyncSession, observation_ids: list[UUID], *, library: str, client_factory) -> DeletionPlan:
    if library not in {KEEP_IN_REPLACE_MEDIA, FORGET}:
        raise AppError("VALIDATION_ERROR", "Choose whether to keep the title in Replace Media or remove it from Medialogue.", status_code=422)
    plan = await plan_deletion(db, observation_ids)
    if plan.blocked:
        raise AppError("DELETE_BLOCKED", " ".join(plan.blocked), status_code=409)

    # qBittorrent first: a torrent left behind on deleted files would only
    # report errors. Files stay until every client has let go of them.
    by_client: dict[UUID, list[PlannedTorrent]] = {}
    for item in plan.torrents:
        by_client.setdefault(item.client_id, []).append(item)
    for client_id, items in by_client.items():
        client = await get_configured_download_client(db, client_id)
        if client is None or not client.enabled:
            raise AppError("DOWNLOAD_CLIENT_NOT_FOUND", "A qBittorrent client holding these files is unavailable or disabled.", status_code=409)
        adapter = client_factory(client.url, client.username or "", client.password or "")
        try:
            for item in items:
                await adapter.remove_torrent(item.info_hash, delete_files=False)
        except Exception as exc:
            raise AppError("UPSTREAM_ERROR", f"{client.name} could not remove the torrents, so no files were deleted: {exc}", status_code=502) from exc
        finally:
            await adapter.close()

    now = datetime.now(timezone.utc)
    for item in plan.torrents:
        observation = await db.get(TorrentClientObservation, item.observation_id)
        if observation is not None:
            observation.is_present = False
            observation.removed_at = now
        torrent = await db.get(Torrent, item.torrent_id)
        if torrent is not None:
            # Removed on purpose: never put back by the re-add task.
            torrent.readd_state = "removed"
            torrent.auto_readded_at = torrent.auto_readded_at or now
        await deactivate_client_tracker_health(db, item.torrent_id, item.client_id)
    await db.commit()

    for folder in plan.folders:
        _delete_path(folder)

    if library == FORGET:
        await _forget(db, plan)
    else:
        await _mark_missing(db, plan)
    await create_event(
        db, "media.deleted", entity_type="torrent", entity_id=plan.torrents[0].torrent_id if plan.torrents else None,
        severity=Severity.WARNING,
        message=f"Deleted {len(plan.folders)} folder{'s' if len(plan.folders) != 1 else ''} and removed "
                f"{len(plan.torrents)} torrent{'s' if len(plan.torrents) != 1 else ''} from Downloads.",
        details={"folders": plan.folders, "torrents": [item.info_hash for item in plan.torrents], "library": library,
                 "titles": [item.title for item in plan.titles]},
    )
    await db.commit()
    return plan


async def _deleted_directories(db: AsyncSession, plan: DeletionPlan) -> list[MediaDirectory]:
    rows = (await db.scalars(select(MediaDirectory).options(selectinload(MediaDirectory.files)))).all()
    return [row for row in rows if any(_inside(row.resolved_path, folder) for folder in plan.folders)]


async def _mark_missing(db: AsyncSession, plan: DeletionPlan) -> None:
    """Keep the titles; their files are gone, so Replace Media lists them as Missing."""

    from app.services.shows import refresh_episode_presence

    episode_ids: set[UUID] = set()
    for directory in await _deleted_directories(db, plan):
        directory.exists = False
        for media_file in directory.files:
            media_file.exists = False
            maps = (await db.scalars(select(EpisodeMediaMap).where(EpisodeMediaMap.media_file_id == media_file.id))).all()
            episode_ids.update(item.episode_id for item in maps)
    for release_id in plan.movie_release_ids:
        release = await db.get(MovieRelease, release_id)
        if release is not None and release.release_state in {ReleaseState.CURRENT, ReleaseState.DUPLICATE, ReleaseState.CONFLICT}:
            release.release_state = ReleaseState.MISSING
    for release_id in plan.show_release_ids:
        release = await db.get(ShowRelease, release_id)
        if release is not None and release.release_state in {ReleaseState.CURRENT, ReleaseState.DUPLICATE, ReleaseState.CONFLICT}:
            release.release_state = ReleaseState.MISSING
    await db.flush()
    from app.models.domain import Episode
    for episode_id in episode_ids:
        episode = await db.get(Episode, episode_id)
        if episode is not None:
            await refresh_episode_presence(db, episode, emit_event=False)


async def _forget(db: AsyncSession, plan: DeletionPlan) -> None:
    """Remove the titles from Medialogue, as far as this deletion reaches."""

    for directory in await _deleted_directories(db, plan):
        await db.delete(directory)
    await db.flush()
    for title in plan.titles:
        if title.kind == "movie":
            # Another edition kept elsewhere keeps the movie; only these releases go.
            others = (await db.scalars(select(MovieRelease.id).where(
                MovieRelease.movie_id == title.id, MovieRelease.id.not_in(plan.movie_release_ids),
                MovieRelease.release_state.in_([ReleaseState.CURRENT, ReleaseState.MISSING])))).all()
            if others:
                await db.execute(delete(MovieRelease).where(MovieRelease.id.in_(plan.movie_release_ids), MovieRelease.movie_id == title.id))
            else:
                movie = await db.get(Movie, title.id)
                if movie is not None:
                    await db.delete(movie)
        else:
            releases = (await db.scalars(select(ShowRelease).where(ShowRelease.id.in_(plan.show_release_ids), ShowRelease.show_id == title.id))).all()
            season_ids = {release.season_id for release in releases if release.season_id}
            for release in releases:
                await db.delete(release)
            await db.flush()
            for season_id in season_ids:
                season = await db.get(Season, season_id)
                if season is not None:
                    # Seasons come from TMDB and would return; not wanting it
                    # is what keeps it gone.
                    season.monitored = False
                    await db.execute(delete(UpgradeCandidate).where(UpgradeCandidate.season_id == season_id))
            left = await db.scalar(select(ShowRelease.id).where(ShowRelease.show_id == title.id).limit(1))
            if left is None:
                show = await db.get(Show, title.id)
                if show is not None:
                    await db.delete(show)
    await db.flush()
