"""Replace Media: titles that could not be restored from the torrent backup.

A title can come back after a disk failure only if one of its torrents is
known, still registered with its tracker, and seeded by someone else. Each
reason below is one way that fails; the queue lists every title with one.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.integrations.torznab import TorznabClient, SearchResult
from app.models.domain import (AssociationType, EpisodeMediaMap, Job, JobStatus, MediaDirectory, MediaFile, MediaType,
                               Movie, MovieRelease, MovieReleaseTorrent, ReleaseState, ReplacementReview, Season, Show,
                               ShowRelease, ShowReleaseTorrent, StorageRoot, Torrent, TorrentArchiveState, TorrentClientObservation,
                               TorrentTrackerObservation, UpgradeCandidate)
from app.services.integration_state import list_configured_indexers, get_configured_indexer
from app.services.jobs import run_job, update_job
from app.services.release_source import ReleaseSource, source_of_releases, source_of_torrents
from app.services.format_breakdown import comparison_formats
from app.services.quality_profiles import profiles_cached
from app.services.upgrade_candidates import (UpgradeTarget, _movie_targets, _show_targets, _search_target,
    _persist_candidate, _evaluation_policy, _score_candidate)


SEARCH_JOB_TYPE = "search_media_replacements"
EVIDENCE_WINDOW = timedelta(hours=2)
ACTIVE_STATES = ("submitted", "downloading", "validating", "handoff_attention", "cleanup_required")
#: States that wait for the user rather than for qBittorrent.
ACTION_STATES = {"handoff_attention", "cleanup_required"}

#: The torrent is not in qBittorrent, so nothing can check its tracker or swarm.
NOT_WATCHED = "not_watched"
#: Nobody but this client has seeded it for a long time.
NO_SEEDERS = "no_seeders"
#: No tracker has answered for a long time: the site may be gone.
TRACKER_GONE = "tracker_gone"
#: Every tracker confirmed the torrent was deleted.
REMOVED_FROM_TRACKER = "removed_from_tracker"
#: The files were never linked to any torrent.
NO_TORRENT = "no_torrent"
#: When a title's releases fail in different ways, the cheapest fix leads:
#: re-adding a torrent may clear it, a new torrent is the last resort.
REASON_PRIORITY = (NOT_WATCHED, NO_SEEDERS, TRACKER_GONE, REMOVED_FROM_TRACKER, NO_TORRENT)
REASON_LABELS = {
    NOT_WATCHED: "Not in qBittorrent",
    NO_SEEDERS: "No other seeders",
    TRACKER_GONE: "Tracker not responding",
    REMOVED_FROM_TRACKER: "Removed from tracker",
    NO_TORRENT: "No torrent",
}
#: A torrent removed from qBittorrent this recently may be on its way back
#: (a move, a recheck, a re-add), so it is not listed yet.
NOT_WATCHED_GRACE = timedelta(hours=24)
TRACKER_GONE_AFTER = timedelta(days=7)
NO_SEEDERS_AFTER = timedelta(days=14)


def _aware(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


@dataclass(slots=True)
class ReplacementTarget:
    target: UpgradeTarget
    #: The torrents of the releases that cannot be restored (none for NO_TORRENT).
    torrents: list[Torrent]
    releases: list[MovieRelease | ShowRelease]
    reason: str
    #: When the problem started, where that is known.
    since: datetime | None = None

    @property
    def readdable(self) -> bool:
        """Missing from qBittorrent with an archived .torrent to put back.

        Any one archived torrent brings the files back; cross-seeds share them.
        Without one, re-adding is impossible and the title needs a new torrent
        like any other lost source.
        """

        return self.reason == NOT_WATCHED and any(
            torrent.archive_state == TorrentArchiveState.ARCHIVED and torrent.archive_path for torrent in self.torrents
        )


@dataclass(frozen=True, slots=True)
class SourceProblem:
    reason: str
    since: datetime | None = None


def _pick(problems: list[SourceProblem]) -> SourceProblem:
    return min(problems, key=lambda item: REASON_PRIORITY.index(item.reason))


async def lost_sources(db: AsyncSession) -> dict[UUID, Torrent]:
    """Torrents whose every recently observed client confirms terminal tracker errors.

    Archived-only and stale observations never establish a current source
    failure. Only torrents with at least one recent confirmed terminal tracker
    are loaded, so a large, healthy client costs one small query.
    """

    cutoff = datetime.now(timezone.utc) - EVIDENCE_WINDOW
    candidate_ids = list(
        set(
            await db.scalars(
                select(TorrentTrackerObservation.torrent_id).where(
                    TorrentTrackerObservation.terminal_confirmed.is_(True),
                    TorrentTrackerObservation.health_state == "error",
                    TorrentTrackerObservation.active.is_(True),
                )
            )
        )
    )
    if not candidate_ids:
        return {}
    rows: list[Torrent] = []
    for index in range(0, len(candidate_ids), 500):
        rows.extend(
            (
                await db.scalars(
                    select(Torrent)
                    .options(selectinload(Torrent.observations), selectinload(Torrent.tracker_observations))
                    .where(Torrent.id.in_(candidate_ids[index : index + 500]))
                )
            ).all()
        )
    replacement_ids = {
        UUID(str(value))
        for row in rows
        if (value := (row.metadata_json or {}).get("replacement_candidate_id"))
    }
    finished = set()
    if replacement_ids:
        finished = set(
            await db.scalars(
                select(UpgradeCandidate.id).where(
                    UpgradeCandidate.id.in_(replacement_ids),
                    UpgradeCandidate.validated_at.is_not(None),
                    UpgradeCandidate.execution_state.in_(["completed", "cleanup_required"]),
                )
            )
        )
    lost: dict[UUID, Torrent] = {}
    for row in rows:
        replacement_id = (row.metadata_json or {}).get("replacement_candidate_id")
        if replacement_id and UUID(str(replacement_id)) in finished:
            continue
        live = [item for item in row.observations if item.is_present and _aware(item.last_seen_at) >= cutoff]
        if not live:
            continue
        # Every currently observed client must have exclusively confirmed
        # terminal endpoints. An uninspected second client is uncertainty.
        confirmed = True
        for client in live:
            trackers = [item for item in row.tracker_observations if item.download_client_id == client.download_client_id
                        and item.active and item.health_state != "disabled"]
            if not trackers or any(not item.terminal_confirmed or item.health_state != "error"
                                   or not item.last_checked_at or _aware(item.last_checked_at) < cutoff for item in trackers):
                confirmed = False
                break
        if confirmed:
            lost[row.id] = row
    return lost


def _tracker_gone(torrent: Torrent, live: list[TorrentClientObservation], now: datetime) -> datetime | None:
    """When the trackers last worked, if none has worked for TRACKER_GONE_AFTER.

    Every client holding the torrent must have checked its trackers recently and
    found none working, so a client that has not looked is never taken as proof.
    """

    cutoff = now - EVIDENCE_WINDOW
    last_worked: list[datetime] = []
    for client in live:
        trackers = [item for item in torrent.tracker_observations if item.download_client_id == client.download_client_id
                    and item.active and item.health_state != "disabled"]
        # Only an answered error counts. "Not contacted" is what a paused
        # torrent shows forever, and says nothing about the tracker.
        if not trackers or any(item.health_state != "error" for item in trackers):
            return None
        if any(not item.last_checked_at or _aware(item.last_checked_at) < cutoff for item in trackers):
            return None
        worked = [_aware(item.last_success_at) for item in trackers if item.last_success_at]
        last_worked.append(max(worked) if worked else min(_aware(item.first_seen_at) for item in trackers))
    since = max(last_worked) if last_worked else None
    return since if since is not None and since <= now - TRACKER_GONE_AFTER else None


def classify_torrent(torrent: Torrent, now: datetime, *, confirmed_removed: bool) -> SourceProblem | None:
    """Why this torrent could not restore its media, or None when it could.

    None also covers "not sure yet": a torrent only just removed from
    qBittorrent, or one whose client has stopped reporting, keeps its media out
    of the queue until there is evidence.
    """

    live = [item for item in torrent.observations if item.is_present]
    summary = torrent.tracker_summary or {}
    if not live:
        # The tracker's last word outlives the torrent's place in qBittorrent.
        if summary.get("state") == "unregistered":
            return SourceProblem(REMOVED_FROM_TRACKER, _aware(summary.get("checked_at")))
        gone = [_aware(item.removed_at or item.last_seen_at) for item in torrent.observations]
        gone_at = max((value for value in gone if value), default=None)
        if gone_at is None or gone_at <= now - NOT_WATCHED_GRACE:
            return SourceProblem(NOT_WATCHED, gone_at)
        return None
    if confirmed_removed:
        return SourceProblem(REMOVED_FROM_TRACKER, _aware(summary.get("checked_at")))
    if (since := _tracker_gone(torrent, live, now)) is not None:
        return SourceProblem(TRACKER_GONE, since)
    alone = [_aware(item.alone_since) if item.alone_since else None for item in live]
    if alone and all(value is not None and value <= now - NO_SEEDERS_AFTER for value in alone):
        return SourceProblem(NO_SEEDERS, max(alone))
    return None


@dataclass(slots=True)
class ReleaseProblem:
    problem: SourceProblem
    torrents: list[Torrent]


async def release_problems(
    db: AsyncSession,
    *,
    movie_ids: set[UUID] | None = None,
    show_ids: set[UUID] | None = None,
) -> tuple[dict[UUID, ReleaseProblem], set[UUID], set[UUID]]:
    """Every current release that could not be restored, with why.

    A release is fine while any one of its torrents is fine: cross-seeds of the
    same payload protect each other. A release whose only link is a download
    still arriving is left alone until it lands.
    """

    now = datetime.now(timezone.utc)
    active_states = [ReleaseState.CURRENT, ReleaseState.MISSING]
    releases: dict[UUID, tuple[str, UUID]] = {}
    links: dict[UUID, list[tuple[UUID, AssociationType]]] = {}
    for kind, model, link, link_release, owner in (
        ("movie", MovieRelease, MovieReleaseTorrent, MovieReleaseTorrent.movie_release_id, MovieRelease.movie_id),
        ("show", ShowRelease, ShowReleaseTorrent, ShowReleaseTorrent.show_release_id, ShowRelease.show_id),
    ):
        scope = movie_ids if kind == "movie" else show_ids
        if scope is not None and not scope:
            continue
        statement = select(model.id, owner).where(model.release_state.in_(active_states))
        if scope is not None:
            statement = statement.where(owner.in_(scope))
        for release_id, owner_id in (await db.execute(statement)).all():
            releases[release_id] = (kind, owner_id)
        link_statement = (
            select(link_release, link.torrent_id, link.association_type)
            .join(model, model.id == link_release)
            .where(model.release_state.in_(active_states))
        )
        if scope is not None:
            link_statement = link_statement.where(owner.in_(scope))
        for release_id, torrent_id, association in (await db.execute(link_statement)).all():
            links.setdefault(release_id, []).append((torrent_id, association))

    torrent_ids = list({torrent_id for rows in links.values() for torrent_id, _ in rows})
    torrents: dict[UUID, Torrent] = {}
    for index in range(0, len(torrent_ids), 500):
        for torrent in (await db.scalars(
            select(Torrent)
            .options(selectinload(Torrent.observations), selectinload(Torrent.tracker_observations))
            .where(Torrent.id.in_(torrent_ids[index:index + 500]))
        )).all():
            torrents[torrent.id] = torrent
    removed = await lost_sources(db)
    verdicts = {torrent_id: classify_torrent(torrent, now, confirmed_removed=torrent_id in removed)
                for torrent_id, torrent in torrents.items()}

    problems: dict[UUID, ReleaseProblem] = {}
    affected_movies: set[UUID] = set()
    affected_shows: set[UUID] = set()
    for release_id, (kind, owner_id) in releases.items():
        rows = links.get(release_id, [])
        sources = [torrent_id for torrent_id, association in rows if association != AssociationType.INCOMING]
        if rows and not sources:
            continue
        if not sources:
            problem = ReleaseProblem(SourceProblem(NO_TORRENT), [])
        else:
            found = [verdicts.get(torrent_id) for torrent_id in sources]
            if any(item is None for item in found):
                continue
            problem = ReleaseProblem(_pick([item for item in found if item]), [torrents[item] for item in sources if item in torrents])
        problems[release_id] = problem
        (affected_movies if kind == "movie" else affected_shows).add(owner_id)
    return problems, affected_movies, affected_shows


async def replacement_targets(
    db: AsyncSession,
    *,
    movie_ids: set[UUID] | None = None,
    show_ids: set[UUID] | None = None,
) -> list[ReplacementTarget]:
    """Movie releases and show seasons that could not be restored, with why.

    Only titles with a problem release are scored, instead of every profile and
    release in the library.
    """

    problems, affected_movies, affected_shows = await release_problems(db, movie_ids=movie_ids, show_ids=show_ids)
    if not problems:
        return []
    # A title being upgraded is expected to lose its old torrent; the upgrade
    # owns it until it finishes.
    upgrading_movies: set[UUID] = set()
    upgrading_seasons: set[UUID] = set()
    for movie_id, season_id in (await db.execute(
        select(UpgradeCandidate.movie_id, UpgradeCandidate.season_id).where(
            ~UpgradeCandidate.target_key.startswith("replace:"), UpgradeCandidate.execution_state.in_(list(ACTIVE_STATES)))
    )).all():
        if movie_id:
            upgrading_movies.add(movie_id)
        if season_id:
            upgrading_seasons.add(season_id)
    # Scoring every affected release reads the same few profiles each time.
    with profiles_cached():
        movies, _, _ = await _movie_targets(db, replacement=True, movie_ids=affected_movies - upgrading_movies)
        shows, _, _, _ = await _show_targets(
            db, today=datetime.now(timezone.utc).date(), replacement=True, show_ids=affected_shows
        )
    candidates = [*movies, *(target for target in shows if target.season_id not in upgrading_seasons)]
    release_ids = {
        MovieRelease: [item.release_id for target in movies for item in target.releases],
        ShowRelease: [item.release_id for target in shows for item in target.releases],
    }
    releases: dict[UUID, MovieRelease | ShowRelease] = {}
    for model, ids in release_ids.items():
        for index in range(0, len(ids), 500):
            for release in (
                await db.scalars(
                    select(model).options(selectinload(model.torrents)).where(model.id.in_(ids[index : index + 500]))
                )
            ).all():
                releases[release.id] = release
    result: list[ReplacementTarget] = []
    for target in candidates:
        target_releases = [releases[item.release_id] for item in target.releases if item.release_id in releases]
        found = [problems[release.id] for release in target_releases if release.id in problems]
        if not found:
            continue
        problem = _pick([item.problem for item in found])
        excluded = tuple(sorted({torrent.info_hash.lower() for release in target_releases for torrent in release.torrents}))
        result.append(ReplacementTarget(
            target=replace(target, target_key="replace:" + target.target_key, replacement=True, excluded_hashes=excluded),
            torrents=[torrent for item in found for torrent in item.torrents],
            releases=target_releases,
            reason=problem.reason,
            since=problem.since,
        ))
    return result


async def _reviews(db: AsyncSession, keys: list[str]) -> dict[str, ReplacementReview]:
    if not keys:
        return {}
    rows: dict[str, ReplacementReview] = {}
    for index in range(0, len(keys), 500):
        for row in (await db.scalars(select(ReplacementReview).where(ReplacementReview.target_key.in_(keys[index:index + 500])))).all():
            rows[row.target_key] = row
    return rows


def _dismissed(review: ReplacementReview | None, reason: str) -> bool:
    """Ignored for this reason. A different problem brings the title back."""

    return review is not None and review.ignored_reason == reason


async def _search_scope(db: AsyncSession) -> tuple[bool, set[str]]:
    """(bulk search running, target keys of running single-target searches)."""

    jobs = (
        await db.scalars(
            select(Job).where(Job.job_type == SEARCH_JOB_TYPE, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        )
    ).all()
    keys = {str((job.summary or {}).get("target_key")) for job in jobs if (job.summary or {}).get("target_key")}
    bulk = any(not (job.summary or {}).get("target_key") for job in jobs)
    return bulk, keys


def _tab(item: dict[str, Any]) -> str:
    if item["execution_state"] in ACTIVE_STATES:
        return "in_progress"
    if item["dismissed"]:
        return "dismissed"
    if item["candidate_id"]:
        return "found"
    return "needs_search"


def _item(**values: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "target_key": "", "title": "", "torrent_ids": [], "reason": REASON_LABELS[REMOVED_FROM_TRACKER],
        "reason_code": REMOVED_FROM_TRACKER, "reason_since": None, "readdable": False,
        "dismissed": False, "media_type": None, "movie_id": None, "show_id": None, "season_id": None,
        "season_number": None, "year": None, "poster_ref": None, "current_release": None,
        "current_quality": None, "current_score": None, "media_present": None, "last_searched_at": None,
        "last_search_result": None, "candidate_id": None, "candidate_title": None, "candidate_quality": None,
        "candidate_score": None, "candidate_size": None, "seeders": None, "leechers": None, "indexer": None,
        "indexer_id": None, "execution_state": None, "error": None,
        "searching": False, "root_name": None, "lost_evidence": [], "current_size": None,
        "current_matched_formats": [], "candidate_matched_formats": [], "profile_name": None,
        "download_progress": None, "download_speed": None, "download_eta_seconds": None,
        "current_indexer": None, "current_indexer_id": None, "current_tracker": None,
    }
    base.update(values)
    base["tab"] = _tab(base)
    base["needs_attention"] = base["tab"] in {"needs_search", "found"} or base["execution_state"] in ACTION_STATES
    return base


async def replacing_title_ids(db: AsyncSession) -> set[str]:
    """Movies and seasons Replace Media is handling, without building its rows.

    The same titles the queue lists and does not ignore: a lost source still
    to replace, or a replacement under way. Cheap enough for a polled count.
    """

    ids: set[str] = set()
    targets = await replacement_targets(db)
    reviews = await _reviews(db, [entry.target.target_key for entry in targets])
    for entry in targets:
        # Only titles whose torrent is beyond saving belong to Replace Media alone.
        # One missing from qBittorrent is fixed by re-adding it, and one that
        # never had a torrent gets one from any upgrade, so Upgrade Media keeps
        # offering both.
        if entry.reason not in {NOT_WATCHED, NO_TORRENT} and not _dismissed(reviews.get(entry.target.target_key), entry.reason):
            ids.update(str(value) for value in (entry.target.movie_id, entry.target.season_id) if value)
    for movie_id, season_id in (await db.execute(
        select(UpgradeCandidate.movie_id, UpgradeCandidate.season_id).where(
            UpgradeCandidate.target_key.startswith("replace:"), UpgradeCandidate.execution_state.in_(list(ACTIVE_STATES))
        )
    )).all():
        ids.update(str(value) for value in (movie_id, season_id) if value)
    return ids


async def replacement_queue(db: AsyncSession, *, details: bool = True) -> list[dict[str, Any]]:
    """Every title Replace Media lists, with its status.

    ``details=False`` skips what only the page shows (sources, score
    breakdowns, live downloads, folders), for the polled sidebar count.
    """

    targets = await replacement_targets(db)
    bulk_search, searching_keys = await _search_scope(db)
    reviews = await _reviews(db, [entry.target.target_key for entry in targets])

    keys = [entry.target.target_key for entry in targets]
    candidates = {
        row.target_key: row
        for row in (await db.scalars(select(UpgradeCandidate).where(UpgradeCandidate.target_key.in_(keys)))).all()
    } if keys else {}
    movies = {
        movie.id: movie
        for movie in (
            await db.scalars(select(Movie).where(Movie.id.in_({e.target.movie_id for e in targets if e.target.movie_id})))
        ).all()
    }
    season_rows = (
        await db.execute(
            select(Season, Show).join(Show, Show.id == Season.show_id)
            .where(Season.id.in_({e.target.season_id for e in targets if e.target.season_id}))
        )
    ).all()
    seasons = {season.id: (season, show) for season, show in season_rows}

    items: list[dict[str, Any]] = []
    for entry in targets:
        target, torrents = entry.target, entry.torrents
        row = candidates.get(target.target_key)
        review = reviews.get(target.target_key)
        dismissed = _dismissed(review, entry.reason)
        searched_at = review.last_search_at if review else None
        search_result = review.last_search_result if review else None
        movie = movies.get(target.movie_id) if target.movie_id else None
        season, show = seasons.get(target.season_id, (None, None)) if target.season_id else (None, None)
        in_progress = bool(row and row.execution_state in ACTIVE_STATES)
        source = await source_of_torrents(db, torrents) if details else ReleaseSource()
        items.append(_item(
            reason=REASON_LABELS[entry.reason], reason_code=entry.reason, reason_since=entry.since,
            lost_evidence=_lost_evidence(torrents) if entry.reason == REMOVED_FROM_TRACKER else [],
            # Cross-seeds share one payload, so the largest torrent is the size.
            current_size=max((torrent.total_size for torrent in torrents if torrent.total_size), default=None),
            current_indexer=source.indexer_name, current_tracker=source.tracker,
            current_indexer_id=str(source.indexer_id) if source.indexer_id else None,
            target_key=target.target_key, title=target.search_target.title, media_type=target.media_type.value,
            movie_id=str(target.movie_id) if target.movie_id else None,
            show_id=str(show.id) if show else None,
            season_id=str(target.season_id) if target.season_id else None,
            season_number=target.search_target.season,
            year=movie.year if movie else show.year if show else None,
            poster_ref=movie.poster_ref if movie else show.poster_ref if show else None,
            current_release=target.reference.release_name, current_quality=target.reference.quality,
            current_score=max(item.custom_format_score for item in target.releases),
            torrent_ids=[str(torrent.id) for torrent in torrents],
            media_present=all(release.release_state == ReleaseState.CURRENT for release in entry.releases),
            readdable=entry.readdable,
            last_searched_at=searched_at, last_search_result=search_result, dismissed=dismissed,
            candidate_id=str(row.id) if row else None, candidate_title=row.title if row else None,
            candidate_quality=row.quality if row else None,
            candidate_score=row.custom_format_score if row else None,
            seeders=row.seeders if row else None, indexer=row.indexer_name if row else None,
            candidate_size=row.size if row else None, leechers=row.leechers if row else None,
            indexer_id=str(row.indexer_id) if row and row.indexer_id else None,
            execution_state=row.execution_state if row else None, error=row.execution_error if row else None,
            **(await _download_live(db, row) if row and details else {}),
            **(await _breakdown(db, row, current_score_snapshot=target.reference.score_snapshot) if row and details else {}),
            searching=not in_progress and (target.target_key in searching_keys or (bulk_search and not dismissed)),
        ))

    # A lost torrent that belongs to no replaceable title is left out: there is
    # nothing in the library to find a new source for.
    # Keep active handoffs visible after the previous torrent disappears or the
    # replacement becomes the new current release.
    known = {item["candidate_id"] for item in items}
    for row in (await db.scalars(select(UpgradeCandidate).where(
        UpgradeCandidate.target_key.startswith("replace:"), UpgradeCandidate.execution_state.in_(list(ACTIVE_STATES))
    ))).all():
        if str(row.id) in known:
            continue
        # Name the media, not the old release, once the lost source is retired.
        movie = await db.get(Movie, row.movie_id) if row.movie_id else None
        season_show = (await db.execute(select(Season, Show).join(Show, Show.id == Season.show_id)
                                        .where(Season.id == row.season_id))).first() if row.season_id else None
        season, show = season_show if season_show else (None, None)
        current_ids = [UUID(value) for value in row.current_release_ids or []]
        source = ReleaseSource() if not details else await source_of_releases(
            db,
            movie_release_ids=current_ids if row.media_type == MediaType.MOVIES else (),
            show_release_ids=current_ids if row.media_type != MediaType.MOVIES else (),
        )
        items.append(_item(
            current_indexer=source.indexer_name, current_tracker=source.tracker,
            current_indexer_id=str(source.indexer_id) if source.indexer_id else None,
            target_key=row.target_key, title=movie.title if movie else show.title if show else row.current_release_name,
            reason="Replacement in progress", reason_code=None, year=movie.year if movie else show.year if show else None,
            poster_ref=movie.poster_ref if movie else show.poster_ref if show else None,
            show_id=str(show.id) if show else None, season_number=season.season_number if season else None,
            current_release=row.current_release_name,
            candidate_id=str(row.id), candidate_title=row.title, candidate_score=row.custom_format_score,
            candidate_quality=row.quality, current_quality=row.current_quality,
            current_score=row.current_custom_format_score, media_type=row.media_type.value,
            movie_id=str(row.movie_id) if row.movie_id else None,
            season_id=str(row.season_id) if row.season_id else None,
            seeders=row.seeders, indexer=row.indexer_name, candidate_size=row.size, leechers=row.leechers,
            indexer_id=str(row.indexer_id) if row.indexer_id else None,
            execution_state=row.execution_state, error=row.execution_error,
            **(await _download_live(db, row) if details else {}),
            **(await _breakdown(db, row) if details else {}),
        ))
    if details:
        await _attach_root_names(db, items)
    return sorted(items, key=lambda item: (item["dismissed"], not item["needs_attention"], item["title"].casefold()))


def _tracker_host(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value if "//" in value else f"//{value}")
    return parsed.hostname or value


def _lost_evidence(torrents) -> list[dict[str, Any]]:
    """The tracker's own reason for each lost torrent, newest failure first."""

    evidence: list[dict[str, Any]] = []
    for torrent in torrents:
        failing = [
            observation for observation in torrent.tracker_observations
            if observation.terminal_confirmed and observation.health_state == "error" and observation.active
        ]
        if not failing:
            evidence.append({"torrent_name": torrent.name, "tracker": _tracker_host((torrent.tracker_summary or {}).get("tracker"))})
            continue
        observation = max(failing, key=lambda item: item.last_checked_at)
        evidence.append({
            "torrent_name": torrent.name,
            "tracker": _tracker_host(observation.endpoint_display),
            "message": (observation.message or "").strip() or None,
            "last_worked_at": observation.last_success_at,
        })
    return evidence


async def _breakdown(db: AsyncSession, row: UpgradeCandidate, *, current_score_snapshot: dict | None = None) -> dict[str, Any]:
    """Both scores' formats. The current side comes from the live scoring of
    the current release when there is one, the score the row shows beside it."""

    snapshot = dict(row.comparison_snapshot or {})
    if current_score_snapshot:
        snapshot["current"] = {**(snapshot.get("current") or {}), "score_snapshot": current_score_snapshot}
    current, candidate = await comparison_formats(db, snapshot)
    profile_name = (snapshot.get("profile") or {}).get("profile_name") or (current_score_snapshot or {}).get("profile_name")
    return {"current_matched_formats": current, "candidate_matched_formats": candidate, "profile_name": profile_name}


async def _download_live(db: AsyncSession, row: UpgradeCandidate) -> dict[str, Any]:
    """The client's live view of a replacement's download, when it has one."""

    if row.torrent_id is None or row.selected_download_client_id is None:
        return {}
    observation = await db.scalar(select(TorrentClientObservation).where(
        TorrentClientObservation.torrent_id == row.torrent_id,
        TorrentClientObservation.download_client_id == row.selected_download_client_id,
    ))
    if observation is None:
        return {}
    return {
        "download_progress": float(observation.progress) if observation.progress is not None else None,
        "download_speed": int(observation.download_speed) if observation.download_speed is not None else None,
        "download_eta_seconds": int(observation.eta_seconds) if observation.eta_seconds is not None else None,
    }


async def _attach_root_names(db: AsyncSession, items: list[dict[str, Any]]) -> None:
    """Name the media folder of each title, from where its releases sit on disk."""

    movie_ids = {UUID(item["movie_id"]) for item in items if item.get("movie_id")}
    show_ids = {UUID(item["show_id"]) for item in items if item.get("show_id")}
    movie_roots: dict[str, str] = {}
    show_roots: dict[str, str] = {}
    if movie_ids:
        for movie_id, name in (await db.execute(
            select(MovieRelease.movie_id, StorageRoot.name)
            .join(MediaDirectory, MediaDirectory.movie_release_id == MovieRelease.id)
            .join(StorageRoot, StorageRoot.id == MediaDirectory.storage_root_id)
            .where(MovieRelease.movie_id.in_(movie_ids))
        )).all():
            movie_roots.setdefault(str(movie_id), name)
    if show_ids:
        # A show's directory is the show container; its files say which folder.
        for show_id, name in (await db.execute(
            select(ShowRelease.show_id, StorageRoot.name)
            .join(EpisodeMediaMap, EpisodeMediaMap.show_release_id == ShowRelease.id)
            .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
            .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
            .join(StorageRoot, StorageRoot.id == MediaDirectory.storage_root_id)
            .where(ShowRelease.show_id.in_(show_ids))
        )).all():
            show_roots.setdefault(str(show_id), name)
    for item in items:
        if item.get("movie_id"):
            item["root_name"] = movie_roots.get(item["movie_id"])
        elif item.get("show_id"):
            item["root_name"] = show_roots.get(item["show_id"])


async def run_replacement_search(job_id: UUID, *, target_key: str | None = None, client_factory=TorznabClient):
    async def worker(db, job):
        await update_job(db, job, status=JobStatus.RUNNING)
        await db.commit()
        targets = await replacement_targets(db)
        reviews = await _reviews(db, [entry.target.target_key for entry in targets])
        if target_key:
            targets = [entry for entry in targets if entry.target.target_key == target_key]
        else:
            # Ignored issues stay out of bulk searches, and so do torrents only
            # missing from qBittorrent with an archived copy: re-adding is the fix.
            targets = [entry for entry in targets if not entry.readdable
                       and not _dismissed(reviews.get(entry.target.target_key), entry.reason)]
        indexers = await list_configured_indexers(db)
        found = failures = 0
        for position, entry in enumerate(targets):
            best, results, errors, success = await _search_target(db, target=entry.target, indexers=indexers, client_factory=client_factory)
            failures += errors
            if success:
                candidate, _ = await _persist_candidate(db, target=entry.target, candidate=best, search_results=results,
                                                      searched_at=datetime.now(timezone.utc))
                found += int(candidate)
            review = reviews.get(entry.target.target_key)
            if review is None:
                review = ReplacementReview(target_key=entry.target.target_key)
                db.add(review)
                reviews[entry.target.target_key] = review
            review.last_search_at = datetime.now(timezone.utc)
            review.last_search_result = "candidate" if best else "no_match" if success else "failed"
            await update_job(db, job, progress={"percent": round((position + 1) / len(targets) * 100),
                                                "current": position + 1, "total": len(targets)})
            await db.commit()
        await update_job(db, job, status=JobStatus.COMPLETED,
            summary={**(job.summary or {}), "targets": len(targets), "candidates_found": found, "indexer_failures": failures,
                     "message": f"Found {found} suitable replacement(s). No downloads were started."},
            progress={"percent": 100})
    await run_job(job_id, worker, failure_code="REPLACEMENT_SEARCH_FAILED", failure_message="Replacement search failed")


async def revalidate_replacement(db: AsyncSession, row: UpgradeCandidate) -> None:
    # Only the media behind this candidate is re-evaluated, not the library.
    movie_ids: set[UUID] = set()
    show_ids: set[UUID] = set()
    if row.movie_id:
        movie_ids.add(row.movie_id)
    elif row.season_id:
        season = await db.get(Season, row.season_id)
        if season is not None:
            show_ids.add(season.show_id)
    targets = await replacement_targets(db, movie_ids=movie_ids, show_ids=show_ids)
    target = next((entry.target for entry in targets if entry.target.target_key == row.target_key), None)
    if target is None:
        raise AppError("REPLACEMENT_STALE", "The source problem or current media changed. Refresh Replace Media.", status_code=409)
    profile, formats = await _evaluation_policy(db, media_type=target.media_type, entity_id=target.profile_entity_id)
    indexer = await get_configured_indexer(db, row.indexer_id)
    if not indexer or not indexer.enabled:
        raise AppError("INDEXER_UNAVAILABLE", "The replacement indexer is unavailable", status_code=409)
    result = SearchResult(guid=row.guid, title=row.title, download_url=row.download_url,
                          size=row.size, seeders=row.seeders, published=None)
    scored = _score_candidate(target=target, profile=profile, formats=formats, indexer=indexer, result=result)
    if scored is None or scored.custom_format_score != row.custom_format_score or profile.snapshot() != row.comparison_snapshot.get("profile"):
        raise AppError("REPLACEMENT_STALE", "The profile or replacement eligibility changed. Search again before downloading.", status_code=409)
    row.comparison_snapshot = scored.snapshot
