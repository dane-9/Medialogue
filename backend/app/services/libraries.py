"""Libraries: which media folders and titles belong together.

A folder joins a library of its own media type, or none. A folder in no
library is invisible to Medialogue, so one is never taken out of its library
while titles are recorded in it: they would go Missing on the next scans.
Moving a folder to another library moves its titles with it.
"""

from __future__ import annotations

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
