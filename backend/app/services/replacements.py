"""A repair queue driven by confirmed, recent tracker evidence, with shared upgrade execution."""
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
from app.models.domain import (EpisodeMediaMap, Job, JobStatus, MediaDirectory, MediaFile, MediaType, Movie,
                               MovieRelease, MovieReleaseTorrent, ReleaseState, Season, Show, ShowRelease,
                               ShowReleaseTorrent, StorageRoot, Torrent, TorrentClientObservation,
                               TorrentTrackerObservation, UpgradeCandidate)
from app.services.integration_state import list_configured_indexers, get_configured_indexer
from app.services.jobs import run_job, update_job
from app.services.release_source import source_of_releases, source_of_torrents
from app.services.format_breakdown import comparison_formats
from app.services.upgrade_candidates import (UpgradeTarget, _movie_targets, _show_targets, _search_target,
    _persist_candidate, _evaluation_policy, _score_candidate)


SEARCH_JOB_TYPE = "search_media_replacements"
EVIDENCE_WINDOW = timedelta(hours=2)
ACTIVE_STATES = ("submitted", "downloading", "validating", "handoff_attention", "cleanup_required")
#: States that wait for the user rather than for qBittorrent.
ACTION_STATES = {"handoff_attention", "cleanup_required"}


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
    #: The lost torrents that put this target in the queue.
    torrents: list[Torrent]
    releases: list[MovieRelease | ShowRelease]


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


def releases_without_any_healthy_source(releases, lost: dict[UUID, Torrent]) -> list[Torrent]:
    """Return failed torrents only for payloads whose every cross-seed failed.

    A release is still available while any torrent attached to it has a usable
    tracker source. One tracker/indexer losing its copy must not place the
    shared media payload in Replace Media.
    """

    affected: list[Torrent] = []
    for release in releases:
        torrents = list(release.torrents)
        if torrents and all(torrent.id in lost for torrent in torrents):
            affected.extend(torrents)
    return affected


async def _affected_media(db: AsyncSession, torrent_ids: list[UUID]) -> tuple[set[UUID], set[UUID]]:
    """Movies and shows that hold a release linked to any of these torrents."""

    movie_ids: set[UUID] = set()
    show_ids: set[UUID] = set()
    for index in range(0, len(torrent_ids), 500):
        chunk = torrent_ids[index : index + 500]
        movie_ids.update(
            await db.scalars(
                select(MovieRelease.movie_id)
                .join(MovieReleaseTorrent, MovieReleaseTorrent.movie_release_id == MovieRelease.id)
                .where(MovieReleaseTorrent.torrent_id.in_(chunk))
            )
        )
        show_ids.update(
            await db.scalars(
                select(ShowRelease.show_id)
                .join(ShowReleaseTorrent, ShowReleaseTorrent.show_release_id == ShowRelease.id)
                .where(ShowReleaseTorrent.torrent_id.in_(chunk))
            )
        )
    return movie_ids, show_ids


async def replacement_targets(
    db: AsyncSession,
    *,
    lost: dict[UUID, Torrent] | None = None,
    movie_ids: set[UUID] | None = None,
    show_ids: set[UUID] | None = None,
) -> list[ReplacementTarget]:
    """Movie releases and show seasons whose every source is lost.

    Only titles linked to a lost torrent are evaluated, instead of every
    profile and release in the library.
    """

    lost = await lost_sources(db) if lost is None else lost
    if not lost:
        return []
    if movie_ids is None or show_ids is None:
        affected_movies, affected_shows = await _affected_media(db, list(lost))
        movie_ids = affected_movies if movie_ids is None else movie_ids & affected_movies
        show_ids = affected_shows if show_ids is None else show_ids & affected_shows
    movies, _, _ = await _movie_targets(db, replacement=True, movie_ids=movie_ids)
    shows, _, _, _ = await _show_targets(
        db, today=datetime.now(timezone.utc).date(), replacement=True, show_ids=show_ids
    )
    candidates = [*movies, *shows]
    movie_release_ids = [item.release_id for target in movies for item in target.releases]
    show_release_ids = [item.release_id for target in shows for item in target.releases]
    releases: dict[UUID, MovieRelease | ShowRelease] = {}
    for model, ids in ((MovieRelease, movie_release_ids), (ShowRelease, show_release_ids)):
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
        affected = releases_without_any_healthy_source(target_releases, lost)
        if not affected:
            continue
        excluded = tuple(sorted({torrent.info_hash.lower() for release in target_releases for torrent in release.torrents}))
        result.append(ReplacementTarget(
            target=replace(target, target_key="replace:" + target.target_key, replacement=True, excluded_hashes=excluded),
            torrents=affected,
            releases=target_releases,
        ))
    return result


def _dismissed(torrents: list[Torrent], now: datetime) -> bool:
    preferences = [(torrent.metadata_json or {}).get("replacement_review") or {} for torrent in torrents]
    return bool(preferences) and all(preference.get("ignored") for preference in preferences)


def _last_search(torrents: list[Torrent]) -> tuple[datetime | None, str | None]:
    """The most recent search across every torrent of a target."""

    latest: tuple[datetime | None, str | None] = (None, None)
    for torrent in torrents:
        metadata = torrent.metadata_json or {}
        when = _aware(metadata.get("replacement_last_search"))
        if when is not None and (latest[0] is None or when > latest[0]):
            latest = (when, metadata.get("replacement_search_result"))
    return latest


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
        "target_key": "", "title": "", "torrent_ids": [], "reason": "Tracker reports torrent removed",
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

    now = datetime.now(timezone.utc)
    ids: set[str] = set()
    for entry in await replacement_targets(db):
        if not _dismissed(entry.torrents, now):
            ids.update(str(value) for value in (entry.target.movie_id, entry.target.season_id) if value)
    for movie_id, season_id in (await db.execute(
        select(UpgradeCandidate.movie_id, UpgradeCandidate.season_id).where(
            UpgradeCandidate.target_key.startswith("replace:"), UpgradeCandidate.execution_state.in_(list(ACTIVE_STATES))
        )
    )).all():
        ids.update(str(value) for value in (movie_id, season_id) if value)
    return ids


async def replacement_queue(db: AsyncSession) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    lost = await lost_sources(db)
    targets = await replacement_targets(db, lost=lost)
    bulk_search, searching_keys = await _search_scope(db)

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
        dismissed = _dismissed(torrents, now)
        searched_at, search_result = _last_search(torrents)
        movie = movies.get(target.movie_id) if target.movie_id else None
        season, show = seasons.get(target.season_id, (None, None)) if target.season_id else (None, None)
        in_progress = bool(row and row.execution_state in ACTIVE_STATES)
        source = await source_of_torrents(db, torrents)
        items.append(_item(
            lost_evidence=_lost_evidence(lost.get(torrent.id, torrent) for torrent in torrents),
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
            last_searched_at=searched_at, last_search_result=search_result, dismissed=dismissed,
            candidate_id=str(row.id) if row else None, candidate_title=row.title if row else None,
            candidate_quality=row.quality if row else None,
            candidate_score=row.custom_format_score if row else None,
            seeders=row.seeders if row else None, indexer=row.indexer_name if row else None,
            candidate_size=row.size if row else None, leechers=row.leechers if row else None,
            indexer_id=str(row.indexer_id) if row and row.indexer_id else None,
            execution_state=row.execution_state if row else None, error=row.execution_error if row else None,
            **(await _download_live(db, row) if row else {}),
            **(await _breakdown(db, row, current_score_snapshot=target.reference.score_snapshot) if row else {}),
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
        source = await source_of_releases(
            db,
            movie_release_ids=current_ids if row.media_type == MediaType.MOVIES else (),
            show_release_ids=current_ids if row.media_type != MediaType.MOVIES else (),
        )
        items.append(_item(
            current_indexer=source.indexer_name, current_tracker=source.tracker,
            current_indexer_id=str(source.indexer_id) if source.indexer_id else None,
            target_key=row.target_key, title=movie.title if movie else show.title if show else row.current_release_name,
            reason="Replacement in progress", year=movie.year if movie else show.year if show else None,
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
            **await _download_live(db, row),
            **await _breakdown(db, row),
        ))
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
        now = datetime.now(timezone.utc)
        targets = await replacement_targets(db)
        if target_key:
            targets = [entry for entry in targets if entry.target.target_key == target_key]
        else:
            # Ignored issues stay out of bulk searches.
            targets = [entry for entry in targets if not _dismissed(entry.torrents, now)]
        indexers = await list_configured_indexers(db)
        found = failures = 0
        for position, entry in enumerate(targets):
            best, results, errors, success = await _search_target(db, target=entry.target, indexers=indexers, client_factory=client_factory)
            failures += errors
            if success:
                candidate, _ = await _persist_candidate(db, target=entry.target, candidate=best, search_results=results,
                                                      searched_at=datetime.now(timezone.utc))
                found += int(candidate)
            for torrent in entry.torrents:
                torrent.metadata_json = {**(torrent.metadata_json or {}), "replacement_last_search": datetime.now(timezone.utc).isoformat(),
                    "replacement_search_result": "candidate" if best else "no_match" if success else "failed"}
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
