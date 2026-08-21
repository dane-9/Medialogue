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


async def add_movie_from_tmdb(
    db: AsyncSession,
    tmdb_id: int,
    *,
    monitored: bool = True,
    client_factory=None,
    api_key: str | None = None,
) -> Movie:
    """Create a library Movie from a TMDB id.

    Movies previously entered the library only by scanning a storage root, which
    meant a title you did not already own could not be interactively searched:
    the search endpoint resolves its target against this table. Adding the movie
    first gives the search, the grab and everything downstream — reconciliation,
    history, problems — a real entity to attach to.

    The movie is created with no releases and no media directory, so it reads as
    Missing until a release is actually grabbed and discovered on disk. Nothing
    is written to the filesystem here.
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

    movie = Movie(
        title=metadata.title,
        sort_title=sort_title(metadata.title),
        year=metadata.year,
        tmdb_id=metadata.tmdb_id,
        overview=metadata.overview,
        poster_ref=metadata.poster_path,
        monitored=monitored,
        # The identity came straight from a TMDB id the operator picked, so it is
        # matched rather than inferred from a filename.
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
        details={"tmdb_id": tmdb_id, "monitored": monitored},
    )
    return movie
