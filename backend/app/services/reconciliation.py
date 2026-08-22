"""Shared observation -> state reconciliation services.

The filesystem and qBittorrent adapters deliberately only report facts.  This
module is the small transactional boundary that turns those facts into movie
release state.  It never creates, moves, renames, copies, or deletes media.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.identity import identity_titles_equivalent
from app.integrations.filesystem import DirectoryObservation, FilesystemObserver
from app.integrations.plex import PlexClient, PlexMediaMatch
from app.models.domain import (
    AssociationType,
    Event,
    IdentityState,
    InteractiveSearchResult,
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
    ParseEvidence,
    PlexMatchMethod,
    PlexMatchState,
    PlexObservation,
    Problem,
    ProblemStatus,
    QualityDefinition,
    ReleaseScope,
    ReleaseState,
    Severity,
    SourceType,
    StorageRoot,
    Torrent,
    TorrentClientObservation,
)
from app.parser import ReleaseParseResult, parse_release_name
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.types import (
    CandidateObservation,
    DecisionKind,
    ExistingRelease,
    PlexState,
)
from app.services.events import create_event, publish_live_event, queue_live_event
from app.services.integration_state import get_configured_plex
from app.services.tmdb import TMDBUnavailable, resolve_movie_identity, resolve_movie_identity_detailed
from app.services.quality_profiles import evaluate_current_release_score


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
    return (left or "").strip().casefold() == (right or "").strip().casefold()


def _identity_label(title: str | None, year: int | None) -> str | None:
    if not title:
        return None
    return f"{title} ({year})" if year is not None else title


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
    # Identity confidence intentionally excludes technical quality. A parsed
    # REMUX/WEB-DL token does not make the title itself more likely to be
    # correct. Technical parsing is tracked separately in the parse snapshot.
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

    # Existing v9 databases may predate the partial unique indexes. PostgreSQL
    # transaction advisory locking still serializes the same logical Problem
    # identity across workers/processes, while the indexes remain a final
    # schema-level guard for fresh installs.
    if db.get_bind().dialect.name == "postgresql":
        identity = f"{reason}\0{entity_type}\0{entity_id or 'global'}".encode()
        advisory_key = int.from_bytes(hashlib.blake2b(identity, digest_size=8).digest(), "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": advisory_key})

    query = select(Problem).where(
        Problem.reason == reason,
        Problem.entity_type == entity_type,
        Problem.status == ProblemStatus.OPEN,
    )
    if entity_id is None:
        query = query.where(Problem.entity_id.is_(None))
    else:
        query = query.where(Problem.entity_id == entity_id)
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
        # Preserve creation/history, but keep affected counts and current
        # evidence fresh when a root or torrent remains unhealthy.
        changed = (
            problem.message != message
            or dict(problem.details or {}) != (details or {})
            or problem.severity != severity
        )
        problem.message = message
        problem.details = details or {}
        problem.severity = severity
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
    problems = (await db.scalars(query.order_by(Problem.created_at.asc()))).all()
    if problems:
        resolved_at = utcnow()
        for problem in problems:
            problem.status = ProblemStatus.RESOLVED
            problem.resolved_at = resolved_at
        # Old builds could race and create duplicate OPEN rows. Resolve every
        # matching row defensively so stale duplicates cannot survive forever.
        problem = problems[0]
        await create_event(
            db,
            "problem.resolved",
            entity_type=entity_type,
            entity_id=entity_id,
            message=f"Resolved problem: {problem.message}",
            details={
                "problem_id": str(problem.id),
                "problem_ids": [str(item.id) for item in problems],
                "count": len(problems),
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
        message=f"Storage root is unavailable: {root.resolved_root_path}",
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
            message=f"Storage root {root.name} is unavailable.",
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
            message=f"Storage root {root.name} is available again.",
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
        committed += 1
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
        by_edition[(release.effective_edition or "").strip().casefold()].append(release)

    duplicate_groups = [group for group in by_edition.values() if len(group) > 1]
    duplicate_ids = {release.id for group in duplicate_groups for release in group}

    # Keep release state consistent with the evidence used to drive Problems.
    # A formerly duplicated release becomes current once it is the sole
    # physical release in its edition slot; an absent duplicate becomes
    # missing rather than remaining permanently tagged as DUPLICATE.
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
        configuration.health = "healthy"
        configuration.last_success_at = utcnow()
        configuration.last_error = None
        for relative_path in candidate.media_files:
            checked_path = str(Path(candidate.path) / relative_path)
            match = await client.find_exact_path(checked_path)
            if match is None:
                continue
            # Exact physical path is sufficient Plex evidence. Plex's own
            # title/year metadata is intentionally not compared with the TMDB
            # identity owned by Medialogue.
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
) -> str:
    """Reconcile one filesystem directory; used by scans and qBit completion."""

    existing_directory = await db.scalar(
        select(MediaDirectory)
        .options(selectinload(MediaDirectory.files), selectinload(MediaDirectory.movie_release))
        .where(MediaDirectory.resolved_path == observation.path)
    )
    if existing_directory is not None:
        # A storage root can be removed from configuration while its durable
        # path inventory is intentionally retained. Re-adding/scanning the
        # same container path should reattach that evidence instead of
        # creating a second logical directory.
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
            if existing_directory.movie_release.release_state == ReleaseState.CONFLICT:
                # CONFLICT was historically only produced by Plex metadata
                # disagreement. Re-run normal reconciliation so old blocked
                # releases heal under the TMDB-authoritative model.
                incoming_release = incoming_release or existing_directory.movie_release
            else:
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

    folder_parse = parse_release_name(observation.name)
    file_parses = [parse_release_name(Path(name).stem) for name in observation.media_files]
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
            parser_version=folder_parse.parser_version,
        )
    )

    if manual_identity and movie is not None:
        title = movie.title
        year = movie.year
        confidence = 1.0

    # Title + year is enough evidence to ask an authoritative identity source.
    # A folder-only parse naturally scores 0.80; do not block it before TMDB or
    # an already-identified Movie gets a chance to confirm the candidate.
    if movie is not None and not manual_identity:
        confidence = max(confidence, 0.95)

    if root.media_type != MediaType.MOVIES or not title or year is None or (confidence < 0.80 and not manual_identity):
        # Parser uncertainty supersedes downstream identity/integration
        # conclusions for this same directory. Do not leave older TMDB/Plex
        # Problems open when their prerequisite identity is no longer valid.
        await resolve_problem(db, "TMDB_IDENTITY_UNRESOLVED", "media_directory", directory.id)
        await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "media_directory", directory.id)
        await open_problem(
            db,
            reason="LOW_CONFIDENCE_MATCH",
            entity_type="media_directory",
            entity_id=directory.id,
            message=(
                f"Could not confidently identify {observation.name}. "
                f"Parser candidate: {_identity_label(title, year) or 'no usable title/year'}."
            ),
            details={
                "path": observation.path,
                "parsed_title": title,
                "parsed_year": year,
                "parsed_identity": _identity_label(title, year),
                "confidence": confidence,
                "parser_warnings": list(folder_parse.warnings),
                "unknown_tokens": list(folder_parse.unknown_tokens),
                "parse": folder_parse.to_dict(),
            },
        )
        return "review"

    # The same directory may have been uncertain on an earlier pass. Healthy
    # parser evidence must close that stale row before later checks continue.
    await resolve_problem(db, "LOW_CONFIDENCE_MATCH", "media_directory", directory.id)

    plex_evidence = await _plex_evidence_for_candidate(db, movie.title if movie else title, movie.year if movie else year, observation)

    # A brand-new logical Movie may be discovered by either an explicit root
    # scan or an in-scope completed qBittorrent torrent, but the filename alone
    # is never sufficient authority. TMDB must establish the external identity
    # and Plex is used only as read-only presence/path evidence.
    if movie is None:
        tmdb_resolution = await resolve_movie_identity_detailed(db, title, year)
        tmdb_match, tmdb_reason = tmdb_resolution.match, tmdb_resolution.reason
        if tmdb_match is None:
            # A missing or unreachable TMDB is a global state, not a fact about
            # this directory. Scans are gated on TMDB being configured, so this
            # only fires on an outage mid-run: fail the job once rather than
            # opening an identical Problem for every directory discovered.
            if tmdb_reason in {"not_configured", "unavailable"}:
                raise TMDBUnavailable(
                    f"TMDB is {tmdb_reason.replace('_', ' ')}; identity cannot be established. "
                    "Check Settings -> Metadata, then run the scan again."
                )
            reason = "TMDB_IDENTITY_UNRESOLVED"
            await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "media_directory", directory.id)
            await open_problem(
                db,
                reason=reason,
                entity_type="media_directory",
                entity_id=directory.id,
                message=f"TMDB could not uniquely identify {_identity_label(title, year) or 'this movie candidate'}.",
                details={
                    "path": observation.path,
                    "parsed_title": title,
                    "parsed_year": year,
                    "parsed_identity": _identity_label(title, year),
                    "tmdb_reason": tmdb_reason,
                    "tmdb_queries": list(tmdb_resolution.queries),
                    "tmdb_candidates": [
                        {
                            "tmdb_id": item.tmdb_id,
                            "title": item.title,
                            "original_title": item.original_title,
                            "year": item.year,
                            "overview": item.overview,
                            "poster_path": item.poster_path,
                        }
                        for item in tmdb_resolution.candidates[:10]
                    ],
                    "parse": folder_parse.to_dict(),
                },
                severity=Severity.ERROR if tmdb_reason == "unavailable" else Severity.WARNING,
            )
            return "review"
        await resolve_problem(db, "TMDB_IDENTITY_UNRESOLVED", "media_directory", directory.id)
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
        # A previously identified movie also proves any directory-level TMDB
        # identity Problems from an older pass are no longer applicable.
        await resolve_problem(db, "TMDB_IDENTITY_UNRESOLVED", "media_directory", directory.id)
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
        preferred_replacement_id = None
        if incoming_release is not None:
            preferred_replacement_id = incoming_release.parse_snapshot.get("replacement_of_release_id")
        candidate = CandidateObservation(
            resolved_path=observation.path,
            inside_allowed_root=True,
            identity_matches=True,
            download_complete=True,
            plex_state=PlexState.MATCHED if plex_evidence.state is PlexState.CONFLICT else plex_evidence.state,
            confidence=confidence,
            edition=folder_parse.edition,
            directory_exists=True,
            filename_identity_matches=confidence >= 0.90,
            preferred_replacement_release_id=str(preferred_replacement_id) if preferred_replacement_id else None,
        )
        existing_for_decision = [
            item for item in _existing_release_rows(movie, list(active))
            if incoming_release is None or item.release_id != str(incoming_release.id)
        ]
        decision = ReconciliationEngine().reconcile_candidate(candidate, existing_for_decision)
        if decision.kind is DecisionKind.PROBLEM:
            for stale_reason in ("AMBIGUOUS_REPLACEMENT_TARGET", "ACTIVE_RELEASE_LIMIT_REACHED", "LOW_CONFIDENCE_MATCH"):
                if stale_reason != decision.reason_code:
                    await resolve_problem(db, stale_reason, "movie", movie.id)
            await open_problem(
                db,
                reason=decision.reason_code,
                entity_type="movie",
                entity_id=movie.id,
                message="Automatic attachment needs review before the new release can become current.",
                details={**decision.details, "path": observation.path},
            )
            return "review"
        for stale_reason in ("AMBIGUOUS_REPLACEMENT_TARGET", "ACTIVE_RELEASE_LIMIT_REACHED", "LOW_CONFIDENCE_MATCH"):
            await resolve_problem(db, stale_reason, "movie", movie.id)
        if decision.kind is DecisionKind.CONFLICT:
            state = ReleaseState.CONFLICT
            result = "conflicts"
        elif decision.kind is DecisionKind.DUPLICATE:
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
        if release is None or decision.kind in {DecisionKind.DUPLICATE, DecisionKind.CONFLICT}:
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name=observation.name,
                parsed_title=title,
                parsed_year=year,
                parsed_edition=folder_parse.edition,
                effective_edition=folder_parse.edition,
                quality_definition_id=quality.id if quality else None,
                release_group=folder_parse.release_group,
                release_state=state,
                became_current_at=utcnow() if state == ReleaseState.CURRENT else None,
                parser_version=folder_parse.parser_version,
                parse_snapshot={**folder_parse.to_dict(), "identity_confidence": confidence},
            )
            db.add(release)
            await db.flush()
        else:
            release.raw_release_name = observation.name
            release.parsed_title = title
            release.parsed_year = year
            release.parsed_edition = folder_parse.edition
            if release.manual_edition_override is None:
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
        await _persist_plex_candidate_evidence(db, movie, release, plex_evidence)
        # Plex metadata cannot dispute a TMDB/manual movie identity.
        await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "movie", movie.id)
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

            # If this torrent originated from Interactive Search, preserve the
            # immutable search-time profile/CF score on the durable Release.
            # qBittorrent names normally preserve the release title, which is
            # the safest available link before tracker-specific hashes are
            # known at search time. Manual qBit additions simply have no
            # original score snapshot.
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

        current_score, current_score_snapshot = await evaluate_current_release_score(
            db,
            media_type=MediaType.MOVIES,
            entity_id=movie.id,
            release_name=release.raw_release_name,
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


async def associate_incoming_torrent(
    db: AsyncSession,
    torrent: Torrent,
    *,
    resolved_path: str | None,
    scope: MediaType,
    complete: bool,
) -> dict[str, Any] | None:
    """Associate a confidently parsed in-scope torrent before completion."""

    parsed = parse_release_name(torrent.name)

    if scope == MediaType.SHOWS:
        title = parsed.identity.title_candidate
        season_number = parsed.identity.season
        if not title or season_number is None:
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
                release_name=release.raw_release_name,
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
            parser_version=parsed.parser_version,
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
        await resolve_problem(db, "LOW_CONFIDENCE_MATCH", "torrent", torrent.id)
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
        # Medialogue's configured storage roots.  Such a path is out of scope,
        # not missing.  Never manufacture a filesystem-access Problem for it.
        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
        await resolve_problem(db, "LOW_CONFIDENCE_MATCH", "torrent", torrent.id)
        return "ignored"
    if not Path(root.resolved_root_path).is_dir():
        # Root health is represented once by ROOT_UNREACHABLE.  Avoid a
        # torrent-per-file Problem storm while the mount itself is offline.
        return "deferred"
    if not path.exists():
        await resolve_problem(db, "LOW_CONFIDENCE_MATCH", "torrent", torrent.id)
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
        await resolve_problem(db, "LOW_CONFIDENCE_MATCH", "torrent", torrent.id)
        await open_problem(
            db,
            reason="TORRENT_PATH_NOT_FOUND",
            entity_type="torrent",
            entity_id=torrent.id,
            message="Could not inspect completed torrent directory.",
            details={"resolved_path": str(path), "error": str(exc)},
        )
        return "problem"
    # A successful directory inspection proves any previous path-not-found
    # condition is over, even if later identity checks expose a different
    # Problem such as LOW_CONFIDENCE_MATCH.
    await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
    parsed = parse_release_name(observation.name)
    files = [parse_release_name(Path(name).stem) for name in observation.media_files]
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
    if confidence < 0.90:
        await open_problem(
            db,
            reason="LOW_CONFIDENCE_MATCH",
            entity_type="torrent",
            entity_id=torrent.id,
            message="Completed torrent directory and filenames could not be confidently matched.",
            details={"path": str(path), "confidence": confidence, "parse": parsed.to_dict()},
        )
        return "problem"
    await resolve_problem(db, "LOW_CONFIDENCE_MATCH", "torrent", torrent.id)
    # Plex metadata never blocks a TMDB-backed movie attachment. Presence/path
    # evidence is retained, while pending/unavailable remain non-authoritative.
    result = await reconcile_movie_directory(
        db,
        root,
        observation,
        torrent=torrent,
        movie_hint=movie,
        incoming_release=incoming_release,
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
        await resolve_problem(db, "TORRENT_SHOW_CONTAINER_REQUIRED", "torrent", torrent.id)
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
        await resolve_problem(db, "TORRENT_SHOW_CONTAINER_REQUIRED", "torrent", torrent.id)
        return "ignored"
    if not Path(root.resolved_root_path).is_dir():
        return "deferred"
    if not path.exists():
        await resolve_problem(db, "TORRENT_SHOW_CONTAINER_REQUIRED", "torrent", torrent.id)
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
        await resolve_problem(db, "TORRENT_SHOW_CONTAINER_REQUIRED", "torrent", torrent.id)
        await open_problem(
            db,
            reason="TORRENT_PATH_NOT_FOUND",
            entity_type="torrent",
            entity_id=torrent.id,
            message="Completed Show torrent does not resolve to a Show container beneath the configured root.",
            details={"resolved_path": str(path), "root": str(root_path)},
        )
        return "problem"

    # Reconcile the top-level Show/release container so nested Season folders
    # remain part of one canonical leave-in-place directory attachment.
    container = root_path / relative.parts[0]
    if not container.is_dir():
        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
        await open_problem(
            db,
            reason="TORRENT_SHOW_CONTAINER_REQUIRED",
            entity_type="torrent",
            entity_id=torrent.id,
            message="A completed Show torrent is directly in the storage-root directory; a containing Show/release directory is required for safe identity reconciliation.",
            details={"resolved_path": str(path), "root": str(root_path)},
        )
        return "problem"
    try:
        observation = FilesystemObserver().inspect_directory(container, root_path)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        await resolve_problem(db, "TORRENT_SHOW_CONTAINER_REQUIRED", "torrent", torrent.id)
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
        parsed = parse_release_name(torrent.name)
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
        await resolve_problem(db, "TORRENT_SHOW_CONTAINER_REQUIRED", "torrent", torrent.id)
    return result


async def reconcile_torrent_disagreements(
    db: AsyncSession,
    torrent: Torrent,
    *,
    qbit_present: bool | None,
) -> None:
    """Surface qBit/media disagreement without changing logical media state.

    ``qbit_present=None`` means qBittorrent still reports the torrent but its
    resolved path is outside every enabled Medialogue storage root.  In that
    state qBittorrent is deliberately out of scope, so stale disagreement
    Problems are cleared rather than reclassified as a removal/missing-path
    fault.
    """

    observations = (
        await db.scalars(
            select(TorrentClientObservation).where(TorrentClientObservation.torrent_id == torrent.id)
        )
    ).all()
    qbit_evidence = [
        {
            "reported_path": item.reported_save_path,
            "resolved_path": item.resolved_save_path,
            "state": item.state,
            "progress": float(item.progress) if item.progress is not None else None,
            "is_present": bool(item.is_present),
            "last_seen_at": item.last_seen_at.isoformat() if item.last_seen_at else None,
        }
        for item in observations
    ]

    movie_links = (await db.scalars(select(MovieReleaseTorrent).where(MovieReleaseTorrent.torrent_id == torrent.id))).all()
    for link in movie_links:
        if link.association_type == AssociationType.INCOMING:
            continue
        release = await db.get(MovieRelease, link.movie_release_id)
        if release is None:
            continue
        directories = (await db.scalars(select(MediaDirectory).where(MediaDirectory.movie_release_id == release.id))).all()
        media_present = any(directory.exists for directory in directories)
        reason = None
        message = ""
        if qbit_present is False and media_present:
            reason = "TORRENT_REMOVED_EXTERNALLY"
            message = "The attached media is present but its qBittorrent torrent was removed externally."
        elif qbit_present is True and not media_present:
            reason = "TORRENT_PATH_NOT_FOUND"
            message = "qBittorrent still reports the torrent, but its attached media path is missing."
        if reason is not None:
            alternate = "TORRENT_PATH_NOT_FOUND" if reason == "TORRENT_REMOVED_EXTERNALLY" else "TORRENT_REMOVED_EXTERNALLY"
            await resolve_problem(db, alternate, "movie_release", release.id)
            await open_problem(
                db,
                reason=reason,
                entity_type="movie_release",
                entity_id=release.id,
                message=message,
                details={
                    "torrent_id": str(torrent.id),
                    "torrent_name": torrent.name,
                    "info_hash": torrent.info_hash,
                    "qbit_observations": qbit_evidence,
                    "mapped_directories": [
                        {
                            "path": directory.resolved_path,
                            "catalogue_exists": bool(directory.exists),
                            "physical_exists": Path(directory.resolved_path).is_dir(),
                            "missing_since": directory.missing_since.isoformat() if directory.missing_since else None,
                        }
                        for directory in directories
                    ],
                },
            )
        else:
            await resolve_problem(db, "TORRENT_REMOVED_EXTERNALLY", "movie_release", release.id)
            await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "movie_release", release.id)

    show_links = (await db.scalars(select(ShowReleaseTorrent).where(ShowReleaseTorrent.torrent_id == torrent.id))).all()
    for link in show_links:
        if link.association_type == AssociationType.INCOMING:
            continue
        release = await db.get(ShowRelease, link.show_release_id)
        if release is None:
            continue
        media_present = bool(
            await db.scalar(
                select(func.count())
                .select_from(EpisodeMediaMap)
                .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
                .where(EpisodeMediaMap.show_release_id == release.id, MediaFile.exists.is_(True))
            )
        )
        mapped_rows = (
            await db.execute(
                select(MediaFile, MediaDirectory)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
                .where(EpisodeMediaMap.show_release_id == release.id)
            )
        ).all()
        reason = None
        message = ""
        if qbit_present is False and media_present:
            reason = "TORRENT_REMOVED_EXTERNALLY"
            message = "The Show media is present but its qBittorrent torrent was removed externally."
        elif qbit_present is True and not media_present:
            reason = "TORRENT_PATH_NOT_FOUND"
            message = "qBittorrent still reports the Show torrent, but none of its mapped media files are present."
        if reason is not None:
            alternate = "TORRENT_PATH_NOT_FOUND" if reason == "TORRENT_REMOVED_EXTERNALLY" else "TORRENT_REMOVED_EXTERNALLY"
            await resolve_problem(db, alternate, "show_release", release.id)
            await open_problem(
                db,
                reason=reason,
                entity_type="show_release",
                entity_id=release.id,
                message=message,
                details={
                    "torrent_id": str(torrent.id),
                    "torrent_name": torrent.name,
                    "info_hash": torrent.info_hash,
                    "qbit_observations": qbit_evidence,
                    "mapped_files": [
                        {
                            "media_file_id": str(media_file.id),
                            "filename": media_file.filename,
                            "path": str(Path(directory.resolved_path) / media_file.relative_path),
                            "catalogue_exists": bool(media_file.exists),
                            "directory_catalogue_exists": bool(directory.exists),
                            "physical_exists": (Path(directory.resolved_path) / media_file.relative_path).is_file(),
                            "missing_since": media_file.missing_since.isoformat() if media_file.missing_since else None,
                        }
                        for media_file, directory in mapped_rows
                    ],
                },
            )
        else:
            await resolve_problem(db, "TORRENT_REMOVED_EXTERNALLY", "show_release", release.id)
            await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "show_release", release.id)


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
