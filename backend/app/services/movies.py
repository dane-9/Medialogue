from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import IdentityState, Movie
from app.services.events import create_event


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sort_title(title: str) -> str:
    """Mirror the sort key reconciliation already applies to discovered titles."""
    lower = title.casefold()
    for article in ("the ", "an ", "a "):
        if lower.startswith(article):
            return f"{title[len(article):]}, {title[: len(article) - 1]}"
    return title


async def create_movie_from_metadata(
    db: AsyncSession,
    *,
    tmdb_id: int,
    title: str,
    year: int | None,
    overview: str | None = None,
    poster_ref: str | None = None,
    monitored: bool = True,
    source: str = "tmdb",
) -> Movie:
    """Create a Movie from already-validated TMDB metadata.

    Manual acquisition search validates and snapshots TMDB metadata before any
    torrent is selected. Reusing that snapshot after qBittorrent accepts the
    release avoids a second remote call at the commit boundary.
    """

    existing = await db.scalar(select(Movie).where(Movie.tmdb_id == tmdb_id))
    if existing is not None:
        return existing
    movie = Movie(
        title=title,
        sort_title=sort_title(title),
        year=year,
        tmdb_id=tmdb_id,
        overview=overview,
        poster_ref=poster_ref,
        monitored=monitored,
        identity_state=IdentityState.MATCHED,
        metadata_refreshed_at=utcnow(),
    )
    db.add(movie)
    await db.flush()
    await create_event(
        db,
        "movie.added",
        entity_type="movie",
        entity_id=movie.id,
        message=f"Added {movie.title} from TMDB.",
        details={"tmdb_id": tmdb_id, "monitored": monitored, "source": source},
    )
    return movie


async def add_movie_from_tmdb(
    db: AsyncSession,
    tmdb_id: int,
    *,
    monitored: bool = True,
    client_factory=None,
    api_key: str | None = None,
) -> Movie:
    """Create a library Movie from a TMDB id.

    This remains the explicit "add without downloading" path used by existing
    callers. New manual acquisitions use an unattached TMDB search instead and
    create the Movie only after qBittorrent accepts the selected release.

    This function itself creates no release and writes nothing to the filesystem.
    """
    existing = await db.scalar(select(Movie).where(Movie.tmdb_id == tmdb_id))
    if existing is not None:
        return existing

    if client_factory is None or api_key is None:
        raise RuntimeError("TMDB client is required to add a Movie")

    client = client_factory(api_key)
    try:
        metadata = await client.get_movie(tmdb_id)
    finally:
        await client.close()

    return await create_movie_from_metadata(
        db,
        tmdb_id=metadata.tmdb_id,
        title=metadata.title,
        year=metadata.year,
        overview=metadata.overview,
        poster_ref=metadata.poster_path,
        monitored=monitored,
    )
