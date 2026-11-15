"""Libraries: which media folders and titles belong together.

A folder joins a library of its own media type, or none. A folder in no
library is invisible to Medialogue, so one is never taken out of its library
while titles are recorded in it: they would go Missing on the next scans.
Moving a folder to another library moves its titles with it.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.domain import (
    Episode,
    EpisodeMediaMap,
    Library,
    MediaDirectory,
    MediaFile,
    Movie,
    MovieRelease,
    Season,
    Show,
    ShowRelease,
    StorageRoot,
)

logger = logging.getLogger(__name__)


async def show_ids_in(db: AsyncSession, condition) -> set[UUID]:
    """Shows that have a directory matching ``condition``.

    A show reaches its directory by two different routes and both have to be
    followed. A scan of a show folder records the folder, the files inside it,
    and an episode match per file, so the link runs
    directory -> file -> episode map -> season -> show. Only a season pack that
    was matched as a whole also sets MediaDirectory.show_release_id, so looking
    at the release link alone finds nothing for an ordinary scanned library.
    """
    ids: set[UUID] = set()
    ids.update(
        (
            await db.scalars(
                select(ShowRelease.show_id)
                .join(MediaDirectory, MediaDirectory.show_release_id == ShowRelease.id)
                .where(condition)
            )
        ).all()
    )
    ids.update(
        (
            await db.scalars(
                select(Season.show_id)
                .join(Episode, Episode.season_id == Season.id)
                .join(EpisodeMediaMap, EpisodeMediaMap.episode_id == Episode.id)
                .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
                .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
                .where(condition)
            )
        ).all()
    )
    return ids


async def movie_ids_in(db: AsyncSession, condition) -> set[UUID]:
    """Movies that have a directory matching ``condition``."""
    return set(
        (
            await db.scalars(
                select(MovieRelease.movie_id)
                .join(MediaDirectory, MediaDirectory.movie_release_id == MovieRelease.id)
                .where(condition)
            )
        ).all()
    )


async def titles_in_root(db: AsyncSession, root_id: UUID) -> tuple[set[UUID], set[UUID]]:
    here = MediaDirectory.storage_root_id == root_id
    return await movie_ids_in(db, here), await show_ids_in(db, here)


async def directory_library_id(db: AsyncSession, directory: MediaDirectory) -> UUID | None:
    """The library a title first found in ``directory`` belongs to: its folder's.

    A directory no longer linked to a folder gets None (the default library).
    A folder in no library is invisible, so no title is added from it.
    """

    if directory.storage_root_id is None:
        return None
    root = await db.get(StorageRoot, directory.storage_root_id)
    if root is None:
        return None
    return root_library_id(root)


def root_library_id(root: StorageRoot) -> UUID:
    """``root``'s library; refused when the folder is in none."""

    if root.library_id is None:
        raise AppError(
            "STORAGE_ROOT_NOT_IN_LIBRARY",
            f"{root.name} is not in a library. Add it to one first.",
            status_code=409,
        )
    return root.library_id


async def note_title_in_other_library(db: AsyncSession, title: Movie | Show, directory: MediaDirectory) -> None:
    """Log a title whose files turned up in another library's folder.

    A title's library is set once, when it is created; scans never move it.
    """

    if directory.storage_root_id is None:
        return
    library_id = await db.scalar(select(StorageRoot.library_id).where(StorageRoot.id == directory.storage_root_id))
    if library_id is not None and title.library_id is not None and library_id != title.library_id:
        logger.warning(
            "title found in another library's media folder",
            extra={"title": title.title, "title_library_id": str(title.library_id), "folder_library_id": str(library_id),
                   "path": directory.resolved_path},
        )


async def get_library(db: AsyncSession, library_id: UUID) -> Library:
    library = await db.get(Library, library_id)
    if library is None:
        raise AppError("LIBRARY_NOT_FOUND", "Library was not found.", status_code=404)
    return library


async def library_title_count(db: AsyncSession, library: Library) -> int:
    model = Movie if library.kind.value == "movies" else Show
    return int(await db.scalar(select(func.count()).select_from(model).where(model.library_id == library.id)) or 0)


async def library_folder_count(db: AsyncSession, library_id: UUID) -> int:
    return int(
        await db.scalar(select(func.count()).select_from(StorageRoot).where(StorageRoot.library_id == library_id)) or 0
    )


async def assign_root(db: AsyncSession, root: StorageRoot, library: Library | None) -> None:
    """Put ``root`` in ``library`` (None: in no library), moving its titles along.

    Flushes, does not commit.
    """

    if library is not None and library.id == root.library_id:
        return
    if library is not None and library.kind != root.media_type:
        raise AppError(
            "LIBRARY_KIND_MISMATCH",
            f"{root.name} holds {root.media_type.value}, so it cannot join {library.name}, a {library.kind.value} library.",
            status_code=409,
        )
    movie_ids, show_ids = await titles_in_root(db, root.id)
    count = len(movie_ids) + len(show_ids)
    if library is None:
        if count:
            raise AppError(
                "STORAGE_ROOT_HAS_TITLES",
                f"{root.name} holds {count} {'title' if count == 1 else 'titles'}. "
                "Move it by adding it to another library, or remove the media folder.",
                status_code=409,
            )
    else:
        if movie_ids:
            await db.execute(update(Movie).where(Movie.id.in_(movie_ids)).values(library_id=library.id))
        if show_ids:
            await db.execute(update(Show).where(Show.id.in_(show_ids)).values(library_id=library.id))
    root.library_id = library.id if library is not None else None
    await db.flush()


async def title_storage_root(db: AsyncSession, title: Movie | Show) -> StorageRoot | None:
    """The folder a new download of ``title`` belongs in.

    The folder its files are in; a title with no files yet (still downloading,
    or Missing) gets the first folder of its library.
    """

    if isinstance(title, Movie):
        ids = await movie_root_ids(db, title.id)
    else:
        ids = await show_root_ids(db, title.id)
    if ids:
        root = await db.scalar(
            select(StorageRoot).where(StorageRoot.id.in_(ids)).order_by(StorageRoot.sort_order, StorageRoot.name).limit(1)
        )
        if root is not None:
            return root
    return await db.scalar(
        select(StorageRoot)
        .where(StorageRoot.library_id == title.library_id, StorageRoot.enabled.is_(True))
        .order_by(StorageRoot.sort_order, StorageRoot.name)
        .limit(1)
    )


async def movie_root_ids(db: AsyncSession, movie_id: UUID) -> set[UUID]:
    return set(
        (
            await db.scalars(
                select(MediaDirectory.storage_root_id)
                .join(MovieRelease, MediaDirectory.movie_release_id == MovieRelease.id)
                .where(MovieRelease.movie_id == movie_id, MediaDirectory.storage_root_id.is_not(None))
            )
        ).all()
    )


async def show_root_ids(db: AsyncSession, show_id: UUID) -> set[UUID]:
    ids: set[UUID] = set(
        (
            await db.scalars(
                select(MediaDirectory.storage_root_id)
                .join(ShowRelease, MediaDirectory.show_release_id == ShowRelease.id)
                .where(ShowRelease.show_id == show_id, MediaDirectory.storage_root_id.is_not(None))
            )
        ).all()
    )
    ids.update(
        (
            await db.scalars(
                select(MediaDirectory.storage_root_id)
                .join(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .join(Episode, Episode.id == EpisodeMediaMap.episode_id)
                .join(Season, Season.id == Episode.season_id)
                .where(Season.show_id == show_id, MediaDirectory.storage_root_id.is_not(None))
            )
        ).all()
    )
    return ids


async def title_category(db: AsyncSession, title: Movie | Show) -> str | None:
    """The qBittorrent category a new download of ``title`` defaults to: its folder's."""

    root = await title_storage_root(db, title)
    return root.category if root is not None else None


async def title_categories(
    db: AsyncSession, movies: list[Movie], shows: list[Show]
) -> tuple[dict[UUID, str | None], dict[UUID, str | None]]:
    """``title_category`` for many titles at once: (by movie id, by show id)."""

    roots = list(await db.scalars(select(StorageRoot).order_by(StorageRoot.sort_order, StorageRoot.name)))
    rank = {root.id: index for index, root in enumerate(roots)}
    by_id = {root.id: root for root in roots}
    library_first: dict[UUID, StorageRoot] = {}
    for root in roots:
        if root.enabled and root.library_id is not None:
            library_first.setdefault(root.library_id, root)

    def pick(found: dict[UUID, set[UUID]], title: Movie | Show) -> str | None:
        ids = [root_id for root_id in found.get(title.id, ()) if root_id in by_id]
        root = by_id[min(ids, key=rank.__getitem__)] if ids else library_first.get(title.library_id)
        return root.category if root is not None else None

    movie_roots: dict[UUID, set[UUID]] = {}
    show_roots: dict[UUID, set[UUID]] = {}
    movie_ids = [movie.id for movie in movies]
    show_ids = [show.id for show in shows]
    for start in range(0, len(movie_ids), 500):
        chunk = movie_ids[start:start + 500]
        for movie_id, root_id in await db.execute(
            select(MovieRelease.movie_id, MediaDirectory.storage_root_id)
            .join(MediaDirectory, MediaDirectory.movie_release_id == MovieRelease.id)
            .where(MovieRelease.movie_id.in_(chunk), MediaDirectory.storage_root_id.is_not(None))
        ):
            movie_roots.setdefault(movie_id, set()).add(root_id)
    for start in range(0, len(show_ids), 500):
        chunk = show_ids[start:start + 500]
        for show_id, root_id in await db.execute(
            select(ShowRelease.show_id, MediaDirectory.storage_root_id)
            .join(MediaDirectory, MediaDirectory.show_release_id == ShowRelease.id)
            .where(ShowRelease.show_id.in_(chunk), MediaDirectory.storage_root_id.is_not(None))
        ):
            show_roots.setdefault(show_id, set()).add(root_id)
        for show_id, root_id in await db.execute(
            select(Season.show_id, MediaDirectory.storage_root_id)
            .join(Episode, Episode.season_id == Season.id)
            .join(EpisodeMediaMap, EpisodeMediaMap.episode_id == Episode.id)
            .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
            .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
            .where(Season.show_id.in_(chunk), MediaDirectory.storage_root_id.is_not(None))
            .distinct()
        ):
            show_roots.setdefault(show_id, set()).add(root_id)
    return (
        {movie.id: pick(movie_roots, movie) for movie in movies},
        {show.id: pick(show_roots, show) for show in shows},
    )
