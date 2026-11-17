"""Change match for a movie's releases: move them to the movie they really are.

A wrong match is nearly always one folder's release name parsed into the wrong
title, so the fix moves that release, with its folders, files and torrents, to
the right movie: one already in the library, or a new one. A rescan keeps a
folder on the release it already belongs to, so the move sticks.

The movie left with no releases is removed without a trace when a scan made it
from those files. When there is any sign it was wanted for its own sake (added
by hand or by a download, tagged, given its own profile, or with a download on
its way) the caller is asked whether to keep it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.domain import (Event, IdentityState, MediaDirectory, MediaProfileOverride, Movie, MovieRelease, MovieTag,
                               PlexObservation, Problem, ReleaseState, UpgradeCandidate)
from app.services.events import create_event
from app.services.libraries import directory_library_id
from app.services.movie_identity import load_tmdb_movie
from app.services.movies import sort_title
from app.services.pending_grabs import pending_movie_grabs
from app.services.reconciliation import refresh_movie_duplicate_problem

#: How a movie came into the library, from its "movie.added" event: these mean
#: someone wanted the title itself. "tmdb" is how both a hand-added movie and an
#: approved New Media folder were recorded before the two were told apart.
WANTED_SOURCES = frozenset({"manual_add", "manual_download", "tmdb"})
ACTIVE_STATES = ("submitted", "linking", "downloading", "validating", "handoff_attention", "cleanup_required")


@dataclass(slots=True)
class RematchOutcome:
    target: Movie
    created: bool
    source_removed: bool
    moved: list[MovieRelease] = field(default_factory=list)


async def keep_reasons(db: AsyncSession, movie: Movie) -> list[str]:
    """Why ``movie`` may be wanted even with no release left; empty when a scan made it."""

    reasons: list[str] = []
    added = await db.scalar(
        select(Event).where(Event.event_type == "movie.added", Event.entity_type == "movie", Event.entity_id == movie.id)
        .order_by(Event.created_at).limit(1)
    )
    source = str((added.details or {}).get("source") or "") if added is not None else ""
    if source == "manual_download":
        reasons.append("It was added by a download you started.")
    elif source in WANTED_SOURCES:
        reasons.append("It was added to the library by hand.")
    if (await pending_movie_grabs(db, [movie.id])).get(movie.id):
        reasons.append("A download for it is on its way.")
    if await db.scalar(select(UpgradeCandidate.id).where(
        UpgradeCandidate.movie_id == movie.id, UpgradeCandidate.execution_state.in_(ACTIVE_STATES)
    ).limit(1)) is not None:
        reasons.append("An upgrade or replacement for it is running.")
    if await db.scalar(select(MovieTag.movie_id).where(MovieTag.movie_id == movie.id).limit(1)) is not None:
        reasons.append("It has tags.")
    if await db.scalar(select(MediaProfileOverride.id).where(MediaProfileOverride.movie_id == movie.id).limit(1)) is not None:
        reasons.append("It has its own quality profile settings.")
    return reasons


async def move_releases(
    db: AsyncSession,
    source: Movie,
    release_ids: list[UUID],
    tmdb_id: int,
    *,
    remove_emptied: bool | None,
    tmdb_client_factory=None,
) -> RematchOutcome:
    """Move releases of ``source`` to the movie with ``tmdb_id``.

    ``remove_emptied`` decides what happens to ``source`` when no release is
    left: None removes it only when nothing says it was wanted, and otherwise
    refuses with REMATCH_KEEP_OR_REMOVE so the caller can ask.
    """

    if not release_ids:
        raise AppError("VALIDATION_ERROR", "Pick at least one release to move.", status_code=422)
    if source.tmdb_id == tmdb_id:
        raise AppError("REMATCH_SAME_TITLE", f"These releases are already matched to {source.title}.", status_code=409)
    releases = list((await db.scalars(
        select(MovieRelease).where(MovieRelease.movie_id == source.id, MovieRelease.id.in_(release_ids))
    )).all())
    if len(releases) != len(set(release_ids)):
        raise AppError("NOT_FOUND", "A release to move does not belong to this movie.", status_code=404)
    moving = {release.id for release in releases}
    for row in (await db.scalars(select(UpgradeCandidate).where(
        UpgradeCandidate.movie_id == source.id, UpgradeCandidate.execution_state.in_(ACTIVE_STATES)
    ))).all():
        if moving & {UUID(str(value)) for value in row.current_release_ids or []}:
            raise AppError(
                "REMATCH_BUSY",
                "An upgrade or replacement of this release is running. Wait for it to finish, then change the match.",
                status_code=409,
            )

    # Replaced and removed releases are history, not something the movie has.
    left = await db.scalar(select(MovieRelease.id).where(
        MovieRelease.movie_id == source.id, MovieRelease.id.not_in(moving),
        MovieRelease.release_state.in_([ReleaseState.CURRENT, ReleaseState.MISSING, ReleaseState.DUPLICATE]),
    ).limit(1))
    emptied = left is None
    remove = False
    if emptied:
        reasons = await keep_reasons(db, source)
        if remove_emptied is None and reasons:
            raise AppError(
                "REMATCH_KEEP_OR_REMOVE",
                f"{source.title} has no other releases. Keep it in the library, or remove it?",
                status_code=409,
                details={"title": source.title, "year": source.year, "reasons": reasons},
            )
        remove = remove_emptied if remove_emptied is not None else True

    target = await db.scalar(select(Movie).where(Movie.tmdb_id == tmdb_id))
    created = target is None
    if target is None:
        metadata = await load_tmdb_movie(db, tmdb_id, tmdb_client_factory)
        directory = await db.scalar(select(MediaDirectory).where(MediaDirectory.movie_release_id.in_(moving)).limit(1))
        library_id = (await directory_library_id(db, directory) if directory is not None else None) or source.library_id
        target = Movie(
            library_id=library_id,
            title=metadata.title,
            sort_title=sort_title(metadata.title),
            year=metadata.year,
            tmdb_id=metadata.tmdb_id,
            overview=metadata.overview,
            poster_ref=metadata.poster_path,
            original_language=getattr(metadata, "original_language", None),
            monitored=source.monitored,
            identity_state=IdentityState.MANUAL,
            manual_identity_override=True,
        )
        db.add(target)
        await db.flush()
        await create_event(db, "movie.added", entity_type="movie", entity_id=target.id,
                           message=f"Added {target.title} by changing a release's match.",
                           details={"tmdb_id": target.tmdb_id, "source": "rematch"})

    names = ", ".join(release.raw_release_name for release in releases)
    for release in releases:
        release.movie_id = target.id
    # Plex matched these files to the old title; it checks them again.
    await db.execute(delete(PlexObservation).where(PlexObservation.movie_release_id.in_(moving)))
    # Upgrades and replacements found for the old title compared against its
    # profile; the new title's searches find their own.
    for row in (await db.scalars(select(UpgradeCandidate).where(UpgradeCandidate.movie_id == source.id))).all():
        if remove or moving & {UUID(str(value)) for value in row.current_release_ids or []}:
            await db.delete(row)
    await db.flush()
    db.expire(source, ["releases"])
    db.expire(target, ["releases"])

    await refresh_movie_duplicate_problem(db, target.id)
    await create_event(db, "movie.release_rematched", entity_type="movie", entity_id=target.id,
                       message=f"Moved {names} here from {source.title}.",
                       details={"from_movie_id": str(source.id), "release_ids": [str(value) for value in moving]})
    if remove:
        # It was never really in the library, so it leaves no history behind.
        await db.execute(delete(Event).where(Event.entity_type == "movie", Event.entity_id == source.id))
        await db.execute(delete(Problem).where(Problem.entity_type == "movie", Problem.entity_id == source.id))
        await db.delete(source)
    else:
        await refresh_movie_duplicate_problem(db, source.id)
        await create_event(db, "movie.release_rematched", entity_type="movie", entity_id=source.id,
                           message=f"Moved {names} to {target.title}.",
                           details={"to_movie_id": str(target.id), "release_ids": [str(value) for value in moving]})
    await db.flush()
    return RematchOutcome(target=target, created=created, source_removed=remove, moved=releases)

