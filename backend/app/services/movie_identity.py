from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.domain import IdentityState, Movie, utcnow
from app.services.tmdb import get_tmdb_configuration


async def load_tmdb_movie(db: AsyncSession, tmdb_id: int, factory: Callable[..., Any] | None = None) -> Any:
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before manually matching a Movie.", status_code=409)
    from app.integrations.tmdb import TMDBClient

    client = (factory or TMDBClient)(configuration.api_key)
    try:
        return await client.get_movie(tmdb_id)
    except Exception as exc:
        raise AppError("TMDB_UNAVAILABLE", f"Could not load the selected TMDB Movie: {exc}", status_code=503) from exc
    finally:
        await client.close()


async def apply_movie_identity(
    db: AsyncSession,
    movie: Movie,
    tmdb_id: int,
    *,
    tmdb_client_factory: Callable[..., Any] | None = None,
    metadata: Any | None = None,
) -> Movie:
    """Re-point an existing Movie at a different TMDB title.

    Identity only: files, releases, torrents and the movie's own id are all
    left alone, so nothing on disk moves and nothing already tracked is lost.
    The record is marked as a manual override so a later metadata refresh does
    not silently undo the choice.

    `metadata` lets a caller that has already fetched the TMDB record pass it
    in rather than paying for a second request.
    """

    if metadata is None:
        metadata = await load_tmdb_movie(db, tmdb_id, tmdb_client_factory)

    # One library record per TMDB id, or two rows would claim the same title
    # and every later match would have to guess between them.
    existing = await db.scalar(select(Movie).where(Movie.tmdb_id == tmdb_id))
    if existing is not None and existing.id != movie.id:
        raise AppError(
            "TMDB_ID_ALREADY_USED",
            "That TMDB Movie is already represented by another library record.",
            status_code=409,
        )

    movie.title = metadata.title
    movie.sort_title = metadata.title.casefold()
    movie.year = metadata.year
    movie.tmdb_id = metadata.tmdb_id
    movie.overview = metadata.overview
    movie.poster_ref = metadata.poster_path
    movie.identity_state = IdentityState.MANUAL
    movie.manual_identity_override = True
    movie.metadata_refreshed_at = utcnow()
    movie.revision += 1
    return movie
