"""Shared observation -> state reconciliation services.

The filesystem and qBittorrent adapters deliberately only report facts.  This
module is the small transactional boundary that turns those facts into movie
release state.  It never creates, moves, renames, copies, or deletes media.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.identity import identity_titles_equivalent
from app.integrations.filesystem import DirectoryObservation
from app.integrations.plex import PlexClient, PlexMediaMatch
from app.models.domain import (
    AssociationType,
    DiscoveryMode,
    IdentityState,
    InteractiveSearchResult,
    MediaDirectory,
    MediaDiscovery,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    NewMediaState,
    ParseEvidence,
    PlexMatchMethod,
    PlexMatchState,
    PlexObservation,
    Problem,
    ProblemStatus,
    QualityDefinition,
    ReleaseState,
    Severity,
    SourceType,
    StorageRoot,
    Torrent,
    classify_problem_workflow,
)
from app.parser import ReleaseParseResult, parse_release
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.types import (
    CandidateObservation,
    DecisionKind,
    ExistingRelease,
    PlexState,
)
from app.services.events import create_event, publish_live_event, queue_live_event
from app.services.integration_state import get_configured_plex
from app.services.tmdb import TMDBUnavailable, resolve_movie_identity_detailed
from app.services.discoveries import retire_missing_new_media_discovery, stage_media_discovery
from app.services.quality_profiles import evaluate_current_release_score, preferred_release_name


_movie_locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sort_title(title: str) -> str:
    lower = title.casefold()
    for article in ("the ", "an ", "a "):
        if lower.startswith(article):
            return f"{title[len(article):]}, {title[: len(article) - 1]}"
    return title


def _same_edition(left: str | None, right: str | None) -> bool:
    normalize = lambda value: "" if (value or "").strip().casefold() in {"", "theatrical cut"} else (value or "").strip().casefold()
    return normalize(left) == normalize(right)


async def _find_movie_by_identity(db: AsyncSession, title: str, year: int) -> Movie | None:
    """Find an existing Movie using the same punctuation-insensitive identity rules as TMDB/Plex."""

    candidates = (await db.scalars(select(Movie).where(Movie.year == year))).all()
    matches = [item for item in candidates if identity_titles_equivalent(item.title, title)]
    return matches[0] if len(matches) == 1 else None


def _confidence(
    folder: ReleaseParseResult,
    files: list[ReleaseParseResult],
    *,
    has_disc_structure: bool = False,
) -> float:
    if not folder.identity.title_candidate:
        return 0.0
    score = 0.60
    if folder.identity.year is not None:
        score += 0.20
    if any(
        item.identity.title_candidate
        and item.identity.title_candidate.casefold() == folder.identity.title_candidate.casefold()
        and item.identity.year == folder.identity.year
        for item in files
    ):
        score += 0.20
    elif has_disc_structure:
        score += 0.20
    return min(score, 1.0)


async def open_problem(
    db: AsyncSession,
    *,
    reason: str,
    entity_type: str,
    entity_id: UUID | None,
    message: str,
    details: dict[str, Any] | None = None,
    severity: Severity = Severity.WARNING,
) -> Problem:
    """Open/update one durable problem without duplicate OPEN rows.

    The database partial unique indexes are the final concurrency guard. The
    nested transaction lets two workers race safely: the loser rolls back only
    its attempted insert, then updates the row committed by the winner.
    """

    identity_query = select(Problem).where(
        Problem.reason == reason,
        Problem.entity_type == entity_type,
    )
    if entity_id is None:
        identity_query = identity_query.where(Problem.entity_id.is_(None))
    else:
        identity_query = identity_query.where(Problem.entity_id == entity_id)
    suppressed = await db.scalar(
        identity_query.where(Problem.status == ProblemStatus.DISMISSED).order_by(Problem.created_at.desc()).limit(1)
    )
    if suppressed is not None:
        # Dismissal is an explicit admin suppression. Keep its evidence fresh
        # for debugging, but never recreate or reopen the fingerprint.
        changed = (
            suppressed.message != message
            or dict(suppressed.details or {}) != (details or {})
            or suppressed.severity != severity
        )
        suppressed.message = message
        suppressed.details = details or {}
        suppressed.severity = severity
        await db.flush()
        if changed:
            queue_live_event(
                db,
                "problem.updated",
                entity_type=entity_type,
                entity_id=entity_id,
                data={"problem_id": str(suppressed.id), "reason": reason, "suppressed": True},
            )
        return suppressed

    query = identity_query.where(Problem.status == ProblemStatus.OPEN)
    existing_problems = (await db.scalars(query.order_by(Problem.created_at.asc()))).all()
    problem = existing_problems[0] if existing_problems else None
    created = False
    if problem is None:
        candidate = Problem(
            reason=reason,
            entity_type=entity_type,
            entity_id=entity_id,
            message=message,
            details=details or {},
            severity=severity,
            workflow=classify_problem_workflow(reason),
        )
        try:
            async with db.begin_nested():
                db.add(candidate)
                await db.flush()
            problem = candidate
            created = True
        except IntegrityError:
            # Another transaction opened the same condition between our read
            # and insert. Its committed row is now the canonical one.
            problem = await db.scalar(query.order_by(Problem.created_at.asc()).limit(1))
            if problem is None:
                raise

    if not created:
        changed = (
            problem.message != message
            or dict(problem.details or {}) != (details or {})
            or problem.severity != severity
        )
        problem.message = message
        problem.details = details or {}
        problem.severity = severity
        problem.workflow = classify_problem_workflow(reason)
    await db.flush()
    if created:
        await create_event(
            db,
            "problem.created",
            entity_type=entity_type,
            entity_id=entity_id,
            message=message,
            severity=severity,
            details={"problem_id": str(problem.id), "reason": reason, **(details or {})},
        )
    elif changed:
        queue_live_event(
            db,
            "problem.updated",
            entity_type=entity_type,
            entity_id=entity_id,
            data={"problem_id": str(problem.id), "reason": reason},
        )
    return problem


async def resolve_problem(
    db: AsyncSession, reason: str, entity_type: str, entity_id: UUID | None
) -> None:
    query = select(Problem).where(
        Problem.reason == reason,
        Problem.entity_type == entity_type,
        Problem.status == ProblemStatus.OPEN,
    )
    if entity_id is None:
        query = query.where(Problem.entity_id.is_(None))
    else:
        query = query.where(Problem.entity_id == entity_id)
    problem = await db.scalar(query)
    if problem is not None:
        problem.status = ProblemStatus.RESOLVED
        problem.resolved_at = utcnow()
        await create_event(
            db,
            "problem.resolved",
            entity_type=entity_type,
            entity_id=entity_id,
            message=f"Resolved problem: {problem.message}",
            details={
                "problem_id": str(problem.id),
                "reason": reason,
                "resolution": "evidence_changed",
            },
        )


async def mark_root_unavailable(db: AsyncSession, root: StorageRoot, *, error: str | None = None) -> int:
    """Record one root outage and return affected known-directory count."""

    affected = int(
        await db.scalar(
            select(func.count()).select_from(MediaDirectory).where(MediaDirectory.storage_root_id == root.id)
        )
        or 0
    )
    previous = root.last_health
    root.last_health = "unavailable"
    root.last_health_checked_at = utcnow()
    await open_problem(
        db,
        reason="ROOT_UNREACHABLE",
        entity_type="storage_root",
        entity_id=root.id,
        message=f"Media folder is unavailable: {root.resolved_root_path}",
        details={"path": root.resolved_root_path, "affected_count": affected, "error": error},
        severity=Severity.ERROR,
    )
    if previous != "unavailable":
        publish_live_event(
            "storage_root.health",
            entity_type="storage_root",
            entity_id=root.id,
            data={"status": "unavailable", "affected_count": affected, "path": root.resolved_root_path},
        )
        await create_event(
            db,
            "storage_root.unavailable",
            entity_type="storage_root",
            entity_id=root.id,
            message=f"Media folder {root.name} is unavailable.",
            severity=Severity.ERROR,
            details={"reason": "ROOT_UNREACHABLE", "affected_count": affected, "path": root.resolved_root_path},
        )
    return affected


async def mark_root_available(db: AsyncSession, root: StorageRoot) -> None:
    previous = root.last_health
    root.last_health = "available"
    root.last_health_checked_at = utcnow()
    await resolve_problem(db, "ROOT_UNREACHABLE", "storage_root", root.id)
    if previous == "unavailable":
        affected = int(
            await db.scalar(
                select(func.count()).select_from(MediaDirectory).where(MediaDirectory.storage_root_id == root.id)
            )
            or 0
        )
        publish_live_event(
            "storage_root.health",
            entity_type="storage_root",
            entity_id=root.id,
            data={"status": "available", "affected_count": affected, "path": root.resolved_root_path},
        )
        await create_event(
            db,
            "storage_root.restored",
            entity_type="storage_root",
            entity_id=root.id,
            message=f"Media folder {root.name} is available again.",
            details={"affected_count": affected, "path": root.resolved_root_path},
        )


async def mark_absent_known_directories(
    db: AsyncSession,
    root: StorageRoot,
    seen_paths: set[str],
    *,
    grace_checks: int | None = None,
) -> dict[str, int]:
    """Apply configurable missing grace without treating an outage as absence."""

    threshold = max(1, grace_checks or root.missing_grace_checks or 3)
    rows = (
        await db.scalars(
            select(MediaDirectory)
            .options(selectinload(MediaDirectory.movie_release))
            .where(MediaDirectory.storage_root_id == root.id)
        )
    ).all()
    now = utcnow()
    started = committed = restored = 0
    affected_movie_ids: set[UUID] = set()
    for directory in rows:
        if directory.movie_release is not None:
            affected_movie_ids.add(directory.movie_release.movie_id)
        directory.last_exists_check_at = now
        if directory.resolved_path in seen_paths:
            was_missing = not directory.exists
            directory.exists = True
            directory.last_seen_at = now
            directory.missing_since = None
            directory.missing_check_count = 0
            if was_missing:
                restored += 1
                if directory.movie_release and directory.movie_release.release_state == ReleaseState.MISSING:
                    directory.movie_release.release_state = ReleaseState.CURRENT
                await create_event(
                    db,
                    "media.present",
                    entity_type="media_directory",
                    entity_id=directory.id,
                    message="A previously missing media directory is present again.",
                    details={"path": directory.resolved_path, "root_id": str(root.id)},
                )
            continue
        if not directory.exists:
            await retire_missing_new_media_discovery(db, directory.id)
            continue
        # New Media work goes as soon as the folder does, without waiting out the
        # grace window. The window exists so a briefly unreachable share does
        # not mark real library media missing; a pending discovery has never
        # entered the library, so all the delay achieves is a queue row offering
        # an import that cannot succeed.
        await retire_missing_new_media_discovery(db, directory.id)
        if directory.missing_since is None:
            directory.missing_since = now
            directory.missing_check_count = 1
            started += 1
            continue
        directory.missing_check_count = (directory.missing_check_count or 0) + 1
        if directory.missing_check_count < threshold:
            continue
        directory.exists = False
        committed += 1
        await retire_missing_new_media_discovery(db, directory.id)
        release = directory.movie_release
        if release and release.release_state in {ReleaseState.CURRENT, ReleaseState.DUPLICATE}:
            release.release_state = ReleaseState.MISSING
            await create_event(
                db,
                "media.missing",
                entity_type="movie_release",
                entity_id=release.id,
                message="A known movie directory is missing.",
                severity=Severity.WARNING,
                details={"path": directory.resolved_path, "reason": "PATH_NOT_FOUND", "grace_checks": threshold},
            )
    for movie_id in affected_movie_ids:
        await _refresh_movie_duplicate_problem(db, movie_id)
    return {"started": started, "missing": committed, "restored": restored, "threshold": threshold}


def _existing_release_rows(movie: Movie, releases: list[MovieRelease] | None = None) -> list[ExistingRelease]:
    rows: list[ExistingRelease] = []
    for release in releases if releases is not None else movie.releases:
        directory = next((item for item in release.directories if item.exists), None)
        path = directory.resolved_path if directory else next(
            (item.resolved_path for item in release.directories), ""
        )
        rows.append(
            ExistingRelease(
                release_id=str(release.id),
                resolved_path=path,
                state=release.release_state,
                path_exists=bool(directory),
                edition=release.effective_edition,
            )
        )
    return rows


async def _refresh_movie_duplicate_problem(db: AsyncSession, movie_id: UUID) -> None:
    """Recompute the durable movie duplicate Problem from current disk evidence."""

    releases = (
        await db.scalars(
            select(MovieRelease)
            .options(selectinload(MovieRelease.directories))
            .where(MovieRelease.movie_id == movie_id)
            .order_by(MovieRelease.first_seen_at.asc())
            .with_for_update()
        )
    ).unique().all()
    physically_present = [
        release
        for release in releases
        if release.release_state in {ReleaseState.CURRENT, ReleaseState.DUPLICATE}
        and any(directory.exists for directory in release.directories)
    ]
    by_edition: defaultdict[str, list[MovieRelease]] = defaultdict(list)
    for release in physically_present:
        edition_slot = (release.effective_edition or "").strip().casefold()
        by_edition["" if edition_slot in {"", "theatrical cut"} else edition_slot].append(release)

    duplicate_groups = [group for group in by_edition.values() if len(group) > 1]
    duplicate_ids = {release.id for group in duplicate_groups for release in group}

    for release in releases:
        present = release in physically_present
        if release.id in duplicate_ids:
            if release.release_state in {ReleaseState.CURRENT, ReleaseState.DUPLICATE}:
                release.release_state = ReleaseState.DUPLICATE
        elif release.release_state == ReleaseState.DUPLICATE:
            release.release_state = ReleaseState.CURRENT if present else ReleaseState.MISSING

    if duplicate_groups:
        release_ids = sorted((str(release.id) for group in duplicate_groups for release in group))
        movie = await db.get(Movie, movie_id)
        title = movie.title if movie is not None else "movie"
        await open_problem(
            db,
            reason="DUPLICATE_PHYSICAL_RELEASE",
            entity_type="movie",
            entity_id=movie_id,
            message=f"Duplicate physical release detected for {title}.",
            details={"release_ids": release_ids},
            severity=Severity.WARNING,
        )
    else:
        await resolve_problem(db, "DUPLICATE_PHYSICAL_RELEASE", "movie", movie_id)


async def refresh_movie_duplicate_problem(db: AsyncSession, movie_id: UUID) -> None:
    """Public entry point for workflows that change a release's edition slot."""

    await _refresh_movie_duplicate_problem(db, movie_id)


@dataclass(frozen=True, slots=True)
class PlexCandidateEvidence:
    state: PlexState
    match: PlexMediaMatch | None = None
    checked_path: str | None = None


async def _plex_evidence_for_candidate(
    db: AsyncSession,
    title: str,
    year: int | None,
    candidate: DirectoryObservation,
) -> PlexCandidateEvidence:
    observation = await db.scalar(
        select(PlexObservation)
        .where(
            (PlexObservation.resolved_path == candidate.path)
            | PlexObservation.resolved_path.ilike(candidate.path.rstrip("/") + "/%")
        )
        .order_by(PlexObservation.last_seen_at.desc())
    )
    if observation is not None:
        state = {
            PlexMatchState.MATCHED: PlexState.MATCHED,
            PlexMatchState.PENDING: PlexState.PENDING,
            PlexMatchState.NOT_FOUND: PlexState.NOT_FOUND,
            PlexMatchState.CONFLICT: PlexState.CONFLICT,
            PlexMatchState.UNAVAILABLE: PlexState.UNAVAILABLE,
        }.get(observation.match_state, PlexState.UNKNOWN)
        match = None
        if observation.plex_rating_key and observation.plex_reported_path:
            match = PlexMediaMatch(
                rating_key=observation.plex_rating_key,
                title=observation.plex_title or "",
                year=observation.plex_year,
                edition=observation.plex_edition,
                file_path=observation.plex_reported_path,
            )
        return PlexCandidateEvidence(state=state, match=match, checked_path=observation.resolved_path)

    configuration = await get_configured_plex(db)
    if configuration is not None and not configuration.enabled:
        configuration = None
    if configuration is None:
        return PlexCandidateEvidence(PlexState.PENDING)
    client = PlexClient(configuration.url, configuration.token)
    configuration.last_checked_at = utcnow()
    try:
        await client.health()
        snapshot = await client.library_snapshot()
        configuration.health = "healthy"
        configuration.last_success_at = utcnow()
        configuration.last_error = None
        for relative_path in candidate.media_files:
            checked_path = str(Path(candidate.path) / relative_path)
            match = snapshot.find_exact_path(checked_path)
            if match is None:
                continue
            return PlexCandidateEvidence(
                PlexState.MATCHED,
                match=match,
                checked_path=checked_path,
            )
        return PlexCandidateEvidence(PlexState.PENDING)
    except Exception as exc:
        configuration.health = "unavailable"
        configuration.last_error = str(exc)
        return PlexCandidateEvidence(PlexState.UNAVAILABLE)
    finally:
        await client.close()


async def _persist_plex_candidate_evidence(
    db: AsyncSession,
    movie: Movie,
    release: MovieRelease,
    evidence: PlexCandidateEvidence,
) -> None:
    state_map = {
        PlexState.MATCHED: PlexMatchState.MATCHED,
        PlexState.PENDING: PlexMatchState.PENDING,
        PlexState.NOT_FOUND: PlexMatchState.NOT_FOUND,
        PlexState.CONFLICT: PlexMatchState.CONFLICT,
        PlexState.UNAVAILABLE: PlexMatchState.UNAVAILABLE,
    }
    state = state_map.get(evidence.state)
    if state is None:
        return
    observation = await db.scalar(
        select(PlexObservation).where(PlexObservation.movie_release_id == release.id)
    )
    previous = observation.match_state if observation else None
    if observation is None:
        observation = PlexObservation(
            media_type=MediaType.MOVIES,
            movie_id=movie.id,
            movie_release_id=release.id,
            match_state=state,
        )
        db.add(observation)
    observation.match_state = state
    observation.match_method = PlexMatchMethod.EXACT_PATH if evidence.match is not None else None
    observation.plex_rating_key = evidence.match.rating_key if evidence.match else None
    observation.plex_title = evidence.match.title if evidence.match else None
    observation.plex_year = evidence.match.year if evidence.match else None
    observation.plex_edition = evidence.match.edition if evidence.match else None
    observation.plex_reported_path = evidence.match.file_path if evidence.match else None
    observation.resolved_path = evidence.checked_path
    observation.last_seen_at = utcnow()
    await db.flush()
    if previous != state and state == PlexMatchState.MATCHED:
        await create_event(
            db,
            "plex.verified",
            entity_type="movie",
            entity_id=movie.id,
            message=f"Plex verified {movie.title} using the media path.",
            details={
                "release_id": str(release.id),
                "match_method": "exact_path" if evidence.match else None,
                "plex_rating_key": evidence.match.rating_key if evidence.match else None,
                "path": evidence.checked_path,
            },
        )


async def reconcile_movie_directory(
    db: AsyncSession,
    root: StorageRoot,
    observation: DirectoryObservation,
    *,
    torrent: Torrent | None = None,
    movie_hint: Movie | None = None,
    incoming_release: MovieRelease | None = None,
    manual_identity: bool = False,
    edition_override_active: bool = False,
    manual_edition_override: str | None = None,
    force_new_media: bool = False,
    replacement_of_release_id: str | None = None,
) -> str:
    """Reconcile one filesystem directory; used by scans and qBit completion.

    ``replacement_of_release_id`` is the missing release the administrator
    chose this directory to replace, when Medialogue could not tell.
    """

    existing_directory = await db.scalar(
        select(MediaDirectory)
        .options(selectinload(MediaDirectory.files), selectinload(MediaDirectory.movie_release))
        .where(MediaDirectory.resolved_path == observation.path)
    )
    if existing_directory is not None:
        if existing_directory.storage_root_id is None:
            existing_directory.storage_root_id = root.id
        was_missing = not existing_directory.exists
        existing_directory.exists = True
        existing_directory.last_seen_at = utcnow()
        existing_directory.last_exists_check_at = utcnow()
        existing_directory.missing_since = None
        existing_directory.missing_check_count = 0
        _sync_files(existing_directory, observation)
        if existing_directory.movie_release is not None:
            if existing_directory.movie_release.release_state == ReleaseState.MISSING:
                existing_directory.movie_release.release_state = ReleaseState.CURRENT
                if was_missing:
                    await create_event(
                        db,
                        "media.reappeared",
                        entity_type="movie_release",
                        entity_id=existing_directory.movie_release.id,
                        message="A missing release reappeared at its known path.",
                        details={"path": observation.path},
                    )
            if torrent is not None:
                current_release = existing_directory.movie_release
                # qBittorrent is polled before completed media is
                # reconciled. If the torrent points at a directory that is
                # already registered, the provisional incoming release
                # must not remain visible as an incoming replacement.
                if incoming_release is not None and incoming_release.id != current_release.id:
                    incoming_link = await db.scalar(
                        select(MovieReleaseTorrent).where(
                            MovieReleaseTorrent.movie_release_id == incoming_release.id,
                            MovieReleaseTorrent.torrent_id == torrent.id,
                            MovieReleaseTorrent.association_type == AssociationType.INCOMING,
                        )
                    )
                    if incoming_link is not None:
                        incoming_link.association_type = AssociationType.HISTORICAL
                    has_directory = bool(
                        await db.scalar(
                            select(func.count())
                            .select_from(MediaDirectory)
                            .where(MediaDirectory.movie_release_id == incoming_release.id)
                        )
                    )
                    if not has_directory:
                        incoming_release.release_state = ReleaseState.REMOVED
                        incoming_release.removed_at = utcnow()

                current_link = await db.scalar(
                    select(MovieReleaseTorrent).where(
                        MovieReleaseTorrent.movie_release_id == current_release.id,
                        MovieReleaseTorrent.torrent_id == torrent.id,
                    )
                )
                if current_link is None:
                    db.add(
                        MovieReleaseTorrent(
                            movie_release_id=current_release.id,
                            torrent_id=torrent.id,
                            association_type=AssociationType.ATTACHED,
                        )
                    )
                else:
                    current_link.association_type = AssociationType.ATTACHED
            return "matched"

    folder_parse = parse_release(observation.name)
    effective_candidate_edition = manual_edition_override if edition_override_active else folder_parse.edition
    file_parses = [parse_release(Path(name).stem) for name in observation.media_files]
    confidence = _confidence(
        folder_parse,
        file_parses,
        has_disc_structure=observation.has_dvd_structure or observation.has_bluray_structure,
    )
    title = folder_parse.identity.title_candidate
    year = folder_parse.identity.year
    movie = movie_hint
    if movie is None and title and year is not None:
        movie = await _find_movie_by_identity(db, title, year)

    directory = existing_directory
    if directory is None:
        directory = MediaDirectory(
            storage_root_id=root.id,
            resolved_path=observation.path,
            reported_path=observation.path,
            exists=True,
            last_exists_check_at=utcnow(),
            source_type=SourceType.TORRENT_NAME if torrent is not None else SourceType.FILESYSTEM,
            source_integration_id=torrent.id if torrent is not None else None,
            files=[],
        )
        db.add(directory)
        await db.flush()
        _sync_files(directory, observation)
    db.add(
        ParseEvidence(
            source_type=SourceType.DIRECTORY_NAME,
            source_id=directory.id,
            raw_name=observation.name,
            parse_snapshot={**folder_parse.to_dict(), "identity_confidence": confidence},
        )
    )

    if manual_identity and movie is not None:
        title = movie.title
        year = movie.year
        confidence = 1.0

    if (
        root.media_type == MediaType.MOVIES
        and not manual_identity
        and (force_new_media or (root.discovery_mode == DiscoveryMode.REVIEW and movie is None))
    ):
        discovery = await stage_media_discovery(
            db, directory, folder_parse, media_type=MediaType.MOVIES, title=title, year=year, confidence=confidence
        )
        await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "media_directory", directory.id)
        return "review" if discovery.new_media_state.value == "pending" else "matched"

    # Title + year is enough evidence to ask an authoritative identity source.
    # A folder-only parse naturally scores 0.80; do not block it before TMDB or
    # an already-identified Movie gets a chance to confirm the candidate.
    if movie is not None and not manual_identity:
        confidence = max(confidence, 0.95)

    if root.media_type != MediaType.MOVIES or not title or year is None or (confidence < 0.80 and not manual_identity):
        await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "media_directory", directory.id)
        discovery = await stage_media_discovery(
            db,
            directory,
            folder_parse,
            media_type=MediaType.MOVIES,
            title=title,
            year=year,
            confidence=confidence,
        )
        return "review" if discovery.new_media_state.value == "pending" else "matched"

    plex_evidence = await _plex_evidence_for_candidate(db, movie.title if movie else title, movie.year if movie else year, observation)

    # A brand-new logical Movie may be discovered by either an explicit root
    # scan or an in-scope completed qBittorrent torrent, but the filename alone
    # is never sufficient authority. TMDB must establish the external identity
    # and Plex is used only as read-only presence/path evidence.
    if movie is None:
        tmdb_resolution = await resolve_movie_identity_detailed(db, title, year)
        tmdb_match, tmdb_reason = tmdb_resolution.match, tmdb_resolution.reason
        if tmdb_match is None:
            if tmdb_reason in {"not_configured", "unavailable"}:
                raise TMDBUnavailable(
                    f"TMDB is {tmdb_reason.replace('_', ' ')}; identity cannot be established. "
                    "Check Settings -> TMDB, then run the scan again."
                )
            await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "media_directory", directory.id)
            await stage_media_discovery(
                db,
                directory,
                folder_parse,
                media_type=MediaType.MOVIES,
                title=title,
                year=year,
                confidence=confidence,
                identity_resolution=tmdb_resolution,
            )
            return "review"
        # Exact TMDB identity evidence upgrades a folder-only 0.80 parse to the
        # automatic-attachment threshold without weakening the no-guess rule.
        confidence = max(confidence, 0.95)
        await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "media_directory", directory.id)
        movie = Movie(
            title=tmdb_match.title,
            sort_title=_sort_title(tmdb_match.title),
            year=tmdb_match.year or year,
            tmdb_id=tmdb_match.tmdb_id,
            overview=tmdb_match.overview,
            poster_ref=tmdb_match.poster_path,
            monitored=True,
            identity_state=IdentityState.MATCHED,
            metadata_refreshed_at=utcnow(),
        )
        db.add(movie)
        await db.flush()
    else:
        await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "media_directory", directory.id)

    # Keep all releases loaded before evaluating active slots. The in-process
    # lock prevents overlapping qBit/scan workers; FOR UPDATE protects DB
    # workers in a multi-process deployment.
    async with _movie_locks[movie.id]:
        active = (
            await db.scalars(
                select(MovieRelease)
                .options(selectinload(MovieRelease.directories))
                .where(MovieRelease.movie_id == movie.id)
                .with_for_update()
            )
        ).all()
        preferred_replacement_id = replacement_of_release_id
        if preferred_replacement_id is None and incoming_release is not None:
            preferred_replacement_id = incoming_release.parse_snapshot.get("replacement_of_release_id")
        candidate = CandidateObservation(
            resolved_path=observation.path,
            inside_allowed_root=True,
            identity_matches=True,
            download_complete=True,
            plex_state=PlexState.MATCHED if plex_evidence.state is PlexState.CONFLICT else plex_evidence.state,
            confidence=confidence,
            edition=effective_candidate_edition,
            directory_exists=True,
            filename_identity_matches=confidence >= 0.90,
            preferred_replacement_release_id=str(preferred_replacement_id) if preferred_replacement_id else None,
        )
        existing_for_decision = [
            item for item in _existing_release_rows(movie, list(active))
            if incoming_release is None or item.release_id != str(incoming_release.id)
        ]
        decision = ReconciliationEngine().reconcile_candidate(candidate, existing_for_decision)
        if decision.kind is DecisionKind.REVIEW:
            await stage_media_discovery(
                db,
                directory,
                folder_parse,
                media_type=MediaType.MOVIES,
                title=title,
                year=year,
                confidence=confidence,
            )
            return "review"
        if decision.kind is DecisionKind.PROBLEM:
            await open_problem(
                db,
                reason=decision.reason_code,
                entity_type="movie",
                entity_id=movie.id,
                message="Automatic attachment needs approval before the new release can become current.",
                details={
                    **decision.details,
                    "path": observation.path,
                    # What a later choice needs to finish this same attachment.
                    "directory_id": str(directory.id),
                    "storage_root_id": str(root.id),
                    "torrent_id": str(torrent.id) if torrent is not None else None,
                    "incoming_release_id": str(incoming_release.id) if incoming_release is not None else None,
                },
            )
            return "review"
        await resolve_problem(db, "AMBIGUOUS_REPLACEMENT_TARGET", "movie", movie.id)
        if decision.kind is DecisionKind.DUPLICATE:
            state = ReleaseState.DUPLICATE
            result = "duplicates"
        else:
            state = ReleaseState.CURRENT
            result = "matched"

        if decision.kind is DecisionKind.REPLACEMENT and decision.old_release_id:
            old = next((item for item in active if str(item.id) == decision.old_release_id), None)
            if old is not None:
                old.release_state = ReleaseState.REPLACED
                old.replaced_at = utcnow()
                await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "movie_release", old.id)
        elif decision.kind is DecisionKind.DUPLICATE and decision.old_release_id:
            old = next((item for item in active if str(item.id) == decision.old_release_id), None)
            if old is not None:
                old.release_state = ReleaseState.DUPLICATE

        quality = None
        if folder_parse.quality.canonical:
            quality = await db.scalar(select(QualityDefinition).where(QualityDefinition.name == folder_parse.quality.canonical))
        release = incoming_release
        if release is None or decision.kind is DecisionKind.DUPLICATE:
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name=observation.name,
                parsed_title=title,
                parsed_year=year,
                parsed_edition=folder_parse.edition,
                manual_edition_override=manual_edition_override if edition_override_active else None,
                edition_override_active=edition_override_active,
                effective_edition=effective_candidate_edition,
                quality_definition_id=quality.id if quality else None,
                release_group=folder_parse.release_group,
                release_state=state,
                became_current_at=utcnow() if state == ReleaseState.CURRENT else None,
                    parse_snapshot={**folder_parse.to_dict(), "identity_confidence": confidence},
            )
            db.add(release)
            await db.flush()
        else:
            release.raw_release_name = observation.name
            release.parsed_title = title
            release.parsed_year = year
            release.parsed_edition = folder_parse.edition
            if edition_override_active:
                release.edition_override_active = True
                release.manual_edition_override = manual_edition_override
                release.effective_edition = manual_edition_override
            elif not release.edition_override_active and release.manual_edition_override is None:
                release.effective_edition = folder_parse.edition
            release.quality_definition_id = quality.id if quality else None
            release.release_group = folder_parse.release_group
            release.release_state = state
            release.became_current_at = utcnow() if state == ReleaseState.CURRENT else None
            release.parse_snapshot = {
                **release.parse_snapshot,
                **folder_parse.to_dict(),
                "identity_confidence": confidence,
                "incoming": False,
            }
        directory.movie_release_id = release.id
        discovery = await db.scalar(
            select(MediaDiscovery).where(MediaDiscovery.media_directory_id == directory.id)
        )
        if discovery is not None and discovery.new_media_state != NewMediaState.APPROVED:
            discovery.new_media_state = NewMediaState.APPROVED
            discovery.selected_tmdb_id = movie.tmdb_id
            discovery.approved_media_id = movie.id
            discovery.reviewed_at = utcnow()
        await _persist_plex_candidate_evidence(db, movie, release, plex_evidence)
        # Plex metadata cannot dispute a TMDB/manual movie identity.
        await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "movie", movie.id)
        selected: InteractiveSearchResult | None = None
        if torrent is not None:
            assoc = await db.scalar(
                select(MovieReleaseTorrent).where(
                    MovieReleaseTorrent.movie_release_id == release.id,
                    MovieReleaseTorrent.torrent_id == torrent.id,
                )
            )
            if assoc is None:
                db.add(MovieReleaseTorrent(movie_release_id=release.id, torrent_id=torrent.id, association_type=AssociationType.ATTACHED))
            else:
                assoc.association_type = AssociationType.ATTACHED

            selected = await db.scalar(
                select(InteractiveSearchResult)
                .where(
                    InteractiveSearchResult.target_entity_type == "movie",
                    InteractiveSearchResult.target_entity_id == movie.id,
                    InteractiveSearchResult.selected_at.is_not(None),
                    func.lower(InteractiveSearchResult.title) == torrent.name.casefold(),
                )
                .order_by(InteractiveSearchResult.selected_at.desc())
                .limit(1)
            )
            if selected is not None and release.selection_snapshot is None:
                release.selection_snapshot = dict(selected.selection_snapshot or {})
                release.original_custom_format_score = selected.custom_format_score
            if selected is not None:
                release.tracker_title = selected.title
            elif not release.tracker_title:
                release.tracker_title = str((torrent.metadata_json or {}).get("tracker_title") or "").strip() or None

        current_score, current_score_snapshot = await evaluate_current_release_score(
            db,
            media_type=MediaType.MOVIES,
            entity_id=movie.id,
            release_name=preferred_release_name(release),
            indexer=(
                str(selected.indexer_name).strip()
                if selected is not None and selected.indexer_name
                else str((torrent.metadata_json or {}).get("indexer_name") or "").strip() or None
                if torrent is not None
                else None
            ),
        )
        release.current_custom_format_score = current_score
        release.parse_snapshot = {**dict(release.parse_snapshot or {}), "current_score_snapshot": current_score_snapshot}

        if state != ReleaseState.DUPLICATE:
            await create_event(
                db,
                "release.replaced" if decision.kind is DecisionKind.REPLACEMENT else "media.attached",
                entity_type="movie",
                entity_id=movie.id,
                message=(
                    f"{observation.name} replaced a missing release."
                    if decision.kind is DecisionKind.REPLACEMENT
                    else f"Attached {observation.name}."
                ),
                details={"release_id": str(release.id), "old_release_id": decision.old_release_id, "path": observation.path},
            )
            if decision.kind is DecisionKind.REPLACEMENT:
                await create_event(
                    db,
                    "media.present",
                    entity_type="movie",
                    entity_id=movie.id,
                    message=f"{movie.title} is present after replacement.",
                    details={"release_id": str(release.id), "old_release_id": decision.old_release_id},
                )
        await _refresh_movie_duplicate_problem(db, movie.id)
        return result


def _sync_files(directory: MediaDirectory, observation: DirectoryObservation) -> None:
    existing = {item.relative_path: item for item in directory.files}
    now = utcnow()
    seen: set[str] = set()
    from app.models.domain import MediaFile, MediaRole

    for relative_path in observation.media_files:
        seen.add(relative_path)
        item = existing.get(relative_path)
        if item is None:
            directory.files.append(MediaFile(relative_path=relative_path, filename=Path(relative_path).name, media_role=MediaRole.MOVIE_VIDEO, exists=True))
        else:
            item.exists = True
            item.last_seen_at = now
    for relative_path, item in existing.items():
        if relative_path not in seen:
            item.exists = False
    if observation.has_dvd_structure and "VIDEO_TS" not in existing:
        directory.files.append(MediaFile(relative_path="VIDEO_TS", filename="VIDEO_TS", media_role=MediaRole.DVD_STRUCTURE))
    if observation.has_bluray_structure and "BDMV" not in existing:
        directory.files.append(MediaFile(relative_path="BDMV", filename="BDMV", media_role=MediaRole.BLURAY_STRUCTURE))
