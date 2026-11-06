from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, status
import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.core.problem_types import problem_description
from app.core.integration_config import DownloadClientConfig, get_integration_config_store
from app.db.session import get_db
from app.integrations.qbittorrent import QBittorrentClient
from app.models.auth import AdminUser
from app.models.domain import (
    AssociationType,
    DownloadClient,
    MovieRelease,
    MovieReleaseTorrent,
    Problem,
    ProblemStatus,
    MediaDirectory,
    ShowRelease,
    ShowReleaseTorrent,
    StorageRoot,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
)
from app.schemas.common import Collection, DeleteResponse
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.downloads import (
    DownloadActionFailure,
    DownloadActionRequest,
    DownloadFacetsResponse,
    DownloadActionResponse,
    DownloadClientCategoryResponse,
    DownloadClientCreate,
    DownloadClientResponse,
    DownloadClientSavedTestRequest,
    DownloadClientTestResponse,
    DownloadClientTestRequest,
    DownloadClientUpdate,
    DownloadResponse,
    DownloadScope,
)
from app.services.integration_state import ConfiguredDownloadClient, get_configured_download_client, list_configured_download_clients
from app.services.qbittorrent import (
    refresh_observations_after_action,
    run_all_download_client_polls,
    run_torrent_action,
    run_download_client_poll,
    test_download_client_connection,
)
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job
from app.services.scheduler import request_health_sweep


router = APIRouter(tags=["downloads"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_qbittorrent_client_factory():
    return QBittorrentClient


def _activity_state(observation: TorrentClientObservation) -> str:
    state = str(observation.state or "").casefold()
    progress = float(observation.progress or 0)
    if state.startswith("checking"):
        return "checking"
    if "error" in state or "missing" in state:
        return "error"
    if state == "moving":
        return "moving"
    if state in {"metadl", "forcedmetadl"}:
        return "metadata"
    if "queued" in state:
        return "queued"
    if "stall" in state and progress < 1:
        return "stalled"
    if "pause" in state or "stopped" in state:
        return "paused"
    if progress < 1:
        return "downloading"
    return "seeding"


def _host_only(raw: str | None) -> str | None:
    """Bare host for an announce endpoint, as qBittorrent groups by."""

    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if value.startswith("**"):
        return None
    parsed = urlsplit(value if "//" in value else f"//{value}")
    return parsed.hostname or value


def _tracker_host(torrent: Torrent) -> str | None:
    """Bare host for the torrent's announce endpoint, as qBittorrent groups by.

    ``tracker_summary['tracker']`` is already redacted of any passkey; only the
    scheme and port are dropped here so the label reads as a plain hostname.
    """

    return _host_only((torrent.tracker_summary or {}).get("tracker"))


def _client_response(client: ConfiguredDownloadClient) -> DownloadClientResponse:
    return DownloadClientResponse(
        id=client.id,
        name=client.name,
        url=client.url,
        username=client.username,
        password_configured=bool(client.password),
        scope=DownloadScope(client.scope.value),
        category=client.category,
        tags=list(client.tags or []),
        enabled=client.enabled,
        health=client.health,
        last_health_checked_at=client.last_health_checked_at,
        last_success_at=client.last_success_at,
        latency_ms=client.latency_ms,
        last_error=client.last_error,
        revision=client.revision,
        poll_interval_seconds=client.poll_interval_seconds,
        recent_priority=client.recent_priority,
        older_priority=client.older_priority,
        sequential_order=client.sequential_order,
        first_last_first=client.first_last_first,
        content_layout=client.content_layout,
        completed_download_handling=client.completed_download_handling,
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


def _activity_expression():
    """SQL mirror of :func:`_activity_state`.

    Filtering and sorting by activity has to happen in the database now that a
    page is sliced before responses are built, so the same precedence is
    expressed once here and reused by the list and facet endpoints.
    """

    state = func.lower(func.coalesce(TorrentClientObservation.state, ""))
    progress = func.coalesce(TorrentClientObservation.progress, 0)
    return case(
        (state.like("checking%"), "checking"),
        (or_(state.like("%error%"), state.like("%missing%")), "error"),
        (state == "moving", "moving"),
        (state.in_(("metadl", "forcedmetadl")), "metadata"),
        (state.like("%queued%"), "queued"),
        (and_(state.like("%stall%"), progress < 1), "stalled"),
        (or_(state.like("%pause%"), state.like("%stopped%")), "paused"),
        (progress < 1, "downloading"),
        else_="seeding",
    )


def _tracker_state_expression():
    return func.coalesce(Torrent.tracker_summary["state"].as_string(), "unknown")


def _tracker_host_expression():
    return Torrent.tracker_summary["tracker"].as_string()


def _tag_entries():
    """Table-valued json_each over an observation's tag array."""

    return func.json_each(TorrentClientObservation.tags).table_valued("value").alias("tag_entry")


def _has_any_tag():
    entries = _tag_entries()
    return select(literal(1)).select_from(entries).exists()


def _non_historical_condition():
    """Exclude torrents whose associations are all historical.

    Expressed as EXISTS rather than an id list so it stays a single statement
    and is not bounded by SQLite's variable limit.
    """

    def any_of(table, only_active: bool):
        clauses = [table.torrent_id == Torrent.id]
        if only_active:
            clauses.append(table.association_type != AssociationType.HISTORICAL)
        return select(literal(1)).where(*clauses).exists()

    has_any = or_(any_of(MovieReleaseTorrent, False), any_of(ShowReleaseTorrent, False))
    has_active = or_(any_of(MovieReleaseTorrent, True), any_of(ShowReleaseTorrent, True))
    return or_(~has_any, has_active)


#: Rail rows that map to a plain predicate. Category and tracker rows are keyed
#: by value at request time and handled separately.
def _filter_condition(key: str):
    activity = _activity_expression()
    progress = func.coalesce(TorrentClientObservation.progress, 0)
    download_speed = func.coalesce(TorrentClientObservation.download_speed, 0)
    upload_speed = func.coalesce(TorrentClientObservation.upload_speed, 0)
    tracker_state = _tracker_state_expression()
    tracker_host = _tracker_host_expression()

    if key in {"all", "category:all", "tracker:all"}:
        return None
    if key == "downloading":
        return activity.in_(("downloading", "metadata", "stalled"))
    if key == "seeding":
        return activity == "seeding"
    if key == "completed":
        return progress >= 1
    if key == "running":
        return activity.in_(("downloading", "metadata", "stalled", "checking"))
    if key == "stopped":
        return activity.in_(("paused", "queued"))
    if key == "active":
        return or_(download_speed > 0, upload_speed > 0)
    if key == "stalled":
        return activity == "stalled"
    if key == "checking":
        return activity == "checking"
    if key == "error":
        return activity == "error"
    if key == "moving":
        return activity == "moving"
    if key == "attention":
        return or_(
            activity == "error",
            Torrent.archive_state == TorrentArchiveState.FAILED,
            tracker_state == "unregistered",
        )
    if key == "backed-up":
        return Torrent.archive_state == TorrentArchiveState.ARCHIVED
    if key == "category:none":
        return or_(TorrentClientObservation.category.is_(None), TorrentClientObservation.category == "")
    if key.startswith("category:"):
        return TorrentClientObservation.category == key.removeprefix("category:")
    if key in {"tag:all"}:
        return None
    if key == "tag:none":
        return ~_has_any_tag()
    if key.startswith("tag:"):
        entries = _tag_entries()
        return select(literal(1)).select_from(entries).where(entries.c.value == key.removeprefix("tag:")).exists()
    if key == "tracker:none":
        return or_(tracker_host.is_(None), tracker_host == "")
    if key == "tracker:error":
        return tracker_state.in_(("unregistered", "unavailable"))
    if key == "tracker:warning":
        return tracker_state == "degraded"
    if key.startswith("tracker:"):
        return _tracker_host_matches(tracker_host, key.removeprefix("tracker:"))
    return None


def _like_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _tracker_host_matches(column, host: str):
    """SQL mirror of ``_host_only(column) == host``.

    The stored value is a redacted announce endpoint (``scheme://host:port/path``
    or a bare ``host[:port]``). Matching the host exactly keeps the filter in
    line with the facet counts; a substring match also caught other trackers
    whose hostname merely contained this one.
    """

    escaped = _like_literal(host)
    patterns = [f"%://{escaped}", f"%://{escaped}:%", f"%://{escaped}/%", f"{escaped}:%", f"{escaped}/%"]
    return or_(
        func.lower(column) == host.casefold(),
        *(func.lower(column).like(pattern.casefold(), escape="\\") for pattern in patterns),
    )


#: Sort keys the table offers, mapped to what the database can order by.
def _sort_expression(key: str):
    return {
        "name": Torrent.name,
        "sizeBytes": Torrent.total_size,
        "progress": TorrentClientObservation.progress,
        "state": _activity_expression(),
        "seeds": TorrentClientObservation.seeds,
        "leeches": TorrentClientObservation.leeches,
        "downloadSpeedBytes": TorrentClientObservation.download_speed,
        "uploadSpeedBytes": TorrentClientObservation.upload_speed,
        "etaSeconds": TorrentClientObservation.eta_seconds,
        "trackerState": _tracker_state_expression(),
        "archiveState": Torrent.archive_state,
    }.get(key)


@dataclass(slots=True)
class _DownloadPageContext:
    """Everything one page of download rows needs, fetched in bulk.

    Building this once per request keeps the endpoint at a fixed number of
    queries instead of three per row. The old code also built a response for
    every row before paginating, so a client holding 5000 torrents cost roughly
    15000 queries to return 50 of them.
    """

    associations: dict[uuid.UUID, tuple[Any, Any]]
    problems: dict[uuid.UUID, Problem]
    root_names: dict[uuid.UUID, str]
    payload_groups: dict[uuid.UUID, str]


def _payload_group_ids(
    rows: Sequence[tuple[TorrentClientObservation, Torrent]],
    associations: dict[uuid.UUID, tuple[Any, Any]],
) -> dict[uuid.UUID, str]:
    """Group a page by release, exact payload path, or selected hard-linked files."""

    from app.services.upgrade_execution import _archived_file_signatures, _payload_file_identities

    torrent_ids = {torrent.id for _observation, torrent in rows}
    parent = {torrent_id: torrent_id for torrent_id in torrent_ids}

    def find(value: uuid.UUID) -> uuid.UUID:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(first: uuid.UUID, second: uuid.UUID) -> None:
        left, right = find(first), find(second)
        if left != right:
            parent[max(left, right, key=str)] = min(left, right, key=str)

    release_owner: dict[uuid.UUID, uuid.UUID] = {}
    path_owner: dict[str, uuid.UUID] = {}
    identities: dict[uuid.UUID, set[tuple[int, int]]] = {}
    for observation, torrent in rows:
        association = associations.get(torrent.id)
        if association is not None:
            release_id = association[1].id
            if release_id in release_owner:
                union(torrent.id, release_owner[release_id])
            else:
                release_owner[release_id] = torrent.id

        # A save-path fallback can be the whole library. Only the real
        # qBittorrent content path is specific enough to establish ownership.
        if not str((torrent.metadata_json or {}).get("content_path") or "").strip():
            continue
        resolved = str(observation.resolved_save_path or "").strip()
        if not resolved:
            continue
        path_key = str(Path(resolved).resolve(strict=False)).casefold()
        if path_key in path_owner:
            union(torrent.id, path_owner[path_key])
        else:
            path_owner[path_key] = torrent.id
        signatures = _archived_file_signatures(torrent, observation.download_client_id)
        if signatures:
            identities[torrent.id] = _payload_file_identities([Path(resolved)], file_signatures=signatures)

    identity_rows = [(torrent_id, values) for torrent_id, values in identities.items() if values]
    for position, (torrent_id, values) in enumerate(identity_rows):
        for other_id, other_values in identity_rows[position + 1:]:
            if values & other_values:
                union(torrent_id, other_id)

    return {torrent_id: f"payload:{find(torrent_id)}" for torrent_id in torrent_ids}


def _select_association(target: dict[uuid.UUID, tuple[Any, Any]], rows: Sequence[Any]) -> None:
    """Keep the first non-historical association per torrent, else the first."""

    for association, release in rows:
        current = target.get(association.torrent_id)
        if current is None:
            target[association.torrent_id] = (association, release)
        elif (
            current[0].association_type == AssociationType.HISTORICAL
            and association.association_type != AssociationType.HISTORICAL
        ):
            target[association.torrent_id] = (association, release)


async def _load_page_context(
    db: AsyncSession,
    rows: Sequence[tuple[TorrentClientObservation, Torrent]],
    clients: dict[uuid.UUID, ConfiguredDownloadClient],
) -> _DownloadPageContext:
    movie_ids: list[uuid.UUID] = []
    show_ids: list[uuid.UUID] = []
    for observation, torrent in rows:
        client = clients.get(observation.download_client_id)
        if client is None:
            continue
        (movie_ids if client.scope.value == "movies" else show_ids).append(torrent.id)

    associations: dict[uuid.UUID, tuple[Any, Any]] = {}
    if movie_ids:
        movie_rows = (
            await db.execute(
                select(MovieReleaseTorrent, MovieRelease)
                .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
                .options(
                    selectinload(MovieRelease.quality_definition),
                    selectinload(MovieRelease.directories),
                    selectinload(MovieRelease.movie),
                )
                .where(MovieReleaseTorrent.torrent_id.in_(movie_ids))
                .order_by(MovieReleaseTorrent.created_at.desc())
            )
        ).all()
        _select_association(associations, movie_rows)
    if show_ids:
        show_rows = (
            await db.execute(
                select(ShowReleaseTorrent, ShowRelease)
                .join(ShowRelease, ShowRelease.id == ShowReleaseTorrent.show_release_id)
                .options(
                    selectinload(ShowRelease.quality_definition),
                    selectinload(ShowRelease.directories),
                    selectinload(ShowRelease.show),
                )
                .where(ShowReleaseTorrent.torrent_id.in_(show_ids))
                .order_by(ShowReleaseTorrent.created_at.desc())
            )
        ).all()
        _select_association(associations, show_rows)

    release_ids = [release.id for _, release in associations.values()]
    problems: dict[uuid.UUID, Problem] = {}
    root_names: dict[uuid.UUID, str] = {}
    if release_ids:
        problem_rows = (
            await db.scalars(
                select(Problem)
                .where(
                    Problem.entity_type.in_(("movie_release", "show_release")),
                    Problem.entity_id.in_(release_ids),
                    Problem.status == ProblemStatus.OPEN,
                )
                .order_by(Problem.created_at.desc())
            )
        ).all()
        for problem in problem_rows:
            problems.setdefault(problem.entity_id, problem)

        directory_rows = (
            await db.execute(
                select(MediaDirectory.movie_release_id, MediaDirectory.show_release_id, StorageRoot.name)
                .join(StorageRoot, MediaDirectory.storage_root_id == StorageRoot.id)
                .where(
                    or_(
                        MediaDirectory.movie_release_id.in_(release_ids),
                        MediaDirectory.show_release_id.in_(release_ids),
                    )
                )
            )
        ).all()
        for movie_release_id, show_release_id, name in directory_rows:
            key = movie_release_id or show_release_id
            if key is not None:
                root_names.setdefault(key, name)

    return _DownloadPageContext(
        associations=associations,
        problems=problems,
        root_names=root_names,
        # Path resolution and hard-link inspection stat every payload file;
        # on network storage that must not stall the event loop.
        payload_groups=await asyncio.to_thread(_payload_group_ids, rows, associations),
    )


#: A row is stale once its client has missed this many poll intervals.
_STALE_AFTER_POLLS = 3
_STALE_MINIMUM_SECONDS = 90


def _staleness(observation: TorrentClientObservation, client: ConfiguredDownloadClient) -> dict[str, Any]:
    """Whether the live numbers on this row can still be trusted."""

    if not client.enabled:
        return {"stale": True, "stale_reason": f"{client.name} is disabled; these values are from its last poll."}
    last_seen = observation.last_seen_at
    if last_seen is None:
        return {"stale": False, "stale_reason": None}
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    limit = max(_STALE_MINIMUM_SECONDS, _STALE_AFTER_POLLS * max(5, client.poll_interval_seconds or 30))
    age = (utcnow() - last_seen).total_seconds()
    if age <= limit:
        return {"stale": False, "stale_reason": None}
    minutes = max(1, int(age // 60))
    reason = f"{client.name} has not reported this torrent for {minutes} min"
    if client.last_error:
        reason += f" ({client.last_error})"
    return {"stale": True, "stale_reason": reason + "."}


def _download_response(
    observation: TorrentClientObservation,
    torrent: Torrent,
    client: ConfiguredDownloadClient,
    context: _DownloadPageContext,
) -> DownloadResponse:
    association, release = context.associations.get(torrent.id, (None, None))
    media_state = None
    reconciliation_state = None
    reconciliation_detail = None
    root_name = None
    if release is not None:
        root_name = context.root_names.get(release.id)
        media_state = "present" if any(item.exists for item in release.directories) else "missing"
        reconciliation_state = association.association_type.value
        problem = context.problems.get(release.id)
        if problem is not None:
            reconciliation_state = "conflict"
            reconciliation_detail = problem_description(problem.reason, problem.message)
    return DownloadResponse(
        id=observation.id,
        torrent_id=torrent.id,
        payload_group_id=context.payload_groups.get(torrent.id, f"payload:{torrent.id}"),
        client_id=client.id,
        client_name=client.name,
        scope=DownloadScope(client.scope.value),
        info_hash=torrent.info_hash,
        name=torrent.name,
        total_size=torrent.total_size,
        reported_save_path=observation.reported_save_path,
        resolved_save_path=observation.resolved_save_path,
        state=observation.state,
        progress=float(observation.progress) if observation.progress is not None else None,
        category=observation.category,
        tags=list(observation.tags or []),
        is_present=observation.is_present,
        first_seen_at=observation.first_seen_at,
        last_seen_at=observation.last_seen_at,
        removed_at=observation.removed_at,
        completed_at=torrent.completed_at,
        movie_id=release.movie_id if isinstance(release, MovieRelease) else None,
        show_id=release.show_id if isinstance(release, ShowRelease) else None,
        release_id=release.id if release else None,
        media_title=(
            release.movie.title
            if isinstance(release, MovieRelease)
            else release.show.title
            if isinstance(release, ShowRelease)
            else None
        ),
        media_root_name=root_name,
        media_poster_ref=(
            release.movie.poster_ref
            if isinstance(release, MovieRelease)
            else release.show.poster_ref
            if isinstance(release, ShowRelease)
            else None
        ),
        media_year=(
            release.movie.year
            if isinstance(release, MovieRelease)
            else release.show.year
            if isinstance(release, ShowRelease)
            else None
        ),
        quality=release.quality_definition.name if release and release.quality_definition else None,
        edition=release.effective_edition if isinstance(release, MovieRelease) else None,
        media_state=media_state,
        reconciliation_state=reconciliation_state,
        reconciliation_detail=reconciliation_detail,
        incoming=bool(association and association.association_type == AssociationType.INCOMING),
        incoming_kind=(
            "replacement"
            if release
            and association
            and association.association_type == AssociationType.INCOMING
            and str(release.parse_snapshot.get("incoming_kind") or "release").casefold() == "replacement"
            else "release"
            if release and association and association.association_type == AssociationType.INCOMING
            else None
        ),
        activity=_activity_state(observation),
        **_staleness(observation, client),
        download_speed=observation.download_speed,
        upload_speed=observation.upload_speed,
        eta_seconds=observation.eta_seconds,
        amount_left=observation.amount_left,
        ratio=float(observation.ratio) if observation.ratio is not None else None,
        seeds=observation.seeds,
        seeds_total=observation.seeds_total,
        leeches=observation.leeches,
        leeches_total=observation.leeches_total,
        availability=float(observation.availability) if observation.availability is not None else None,
        archive_state=torrent.archive_state,
        tracker_state=str((torrent.tracker_summary or {}).get("state") or "unknown"),
        tracker_host=_tracker_host(torrent),
    )


@router.get("/download-clients", response_model=Collection[DownloadClientResponse])
async def list_download_clients(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[DownloadClientResponse]:
    rows = sorted(await list_configured_download_clients(db), key=lambda item: item.name.casefold())
    total = len(rows)
    start = (page - 1) * page_size
    rows = rows[start : start + page_size]
    return Collection(
        items=[_client_response(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


@router.post("/download-clients/test", response_model=DownloadClientTestResponse)
async def test_unsaved_download_client(
    payload: DownloadClientTestRequest,
    _: object = Depends(require_csrf),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> DownloadClientTestResponse:
    started = perf_counter()
    adapter = client_factory(str(payload.url).rstrip("/"), payload.username, payload.password)
    try:
        result = await adapter.health()
        return DownloadClientTestResponse(
            status="healthy",
            version=result.get("version"),
            latency_ms=round((perf_counter() - started) * 1000),
        )
    except Exception as exc:
        return DownloadClientTestResponse(status="unavailable", message=str(exc))
    finally:
        await adapter.close()


@router.post("/download-clients", response_model=DownloadClientResponse, status_code=status.HTTP_201_CREATED)
async def create_download_client(
    payload: DownloadClientCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DownloadClientResponse:
    config = get_integration_config_store().save_download_client(
        DownloadClientConfig(
            id=uuid4(),
            name=payload.name,
            url=str(payload.url).rstrip("/"),
            username=payload.username,
            password=payload.password,
            scope=payload.scope.value,
            category=payload.category,
            tags=list(payload.tags),
            enabled=payload.enabled,
            poll_interval_seconds=payload.poll_interval_seconds,
            recent_priority=payload.recent_priority,
            older_priority=payload.older_priority,
            sequential_order=payload.sequential_order,
            first_last_first=payload.first_last_first,
            content_layout=payload.content_layout,
            completed_download_handling=payload.completed_download_handling,
        )
    )
    db.add(DownloadClient(id=config.id))
    await request_health_sweep(db)
    await db.commit()
    client = await get_configured_download_client(db, config.id)
    assert client is not None
    return _client_response(client)


@router.get("/download-clients/{client_id}/categories", response_model=list[DownloadClientCategoryResponse])
async def get_download_client_categories(
    client_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> list[DownloadClientCategoryResponse]:
    client = await get_configured_download_client(db, client_id)
    if client is None or not client.enabled:
        raise AppError("DOWNLOAD_CLIENT_UNAVAILABLE", "Download client is not available.", status_code=404)
    adapter = client_factory(client.url, client.username or "", client.password or "")
    try:
        rows = await adapter.list_categories()
    except Exception as exc:
        raise AppError("DOWNLOAD_CLIENT_UNAVAILABLE", f"Could not load qBittorrent categories: {exc}", status_code=503) from exc
    finally:
        await adapter.close()
    return [
        DownloadClientCategoryResponse(
            name=row.name,
            save_path=row.save_path,
            resolved_save_path=row.resolved_save_path,
            is_default=row.name == (client.category or ""),
        )
        for row in rows
    ]


@router.get("/download-clients/{client_id}", response_model=DownloadClientResponse)
async def get_download_client(
    client_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DownloadClientResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    return _client_response(client)


@router.put("/download-clients/{client_id}", response_model=DownloadClientResponse)
async def update_download_client(
    client_id: UUID,
    payload: DownloadClientUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DownloadClientResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    current = client.config
    values = payload.model_dump(exclude_unset=True)
    values.pop("expected_revision", None)
    password = values.pop("password", None)
    updated = DownloadClientConfig(
        id=current.id,
        name=values.get("name", current.name),
        url=(str(values["url"]).rstrip("/") if values.get("url") is not None else current.url),
        username=values.get("username", current.username),
        password=(password or current.password),
        scope=(values["scope"].value if values.get("scope") is not None else current.scope),
        category=values.get("category", current.category),
        tags=(list(values["tags"]) if values.get("tags") is not None else list(current.tags)),
        enabled=(values["enabled"] if values.get("enabled") is not None else current.enabled),
        revision=current.revision,
        poll_interval_seconds=(values["poll_interval_seconds"] if values.get("poll_interval_seconds") is not None else current.poll_interval_seconds),
        recent_priority=values.get("recent_priority", current.recent_priority),
        older_priority=values.get("older_priority", current.older_priority),
        sequential_order=values.get("sequential_order", current.sequential_order),
        first_last_first=values.get("first_last_first", current.first_last_first),
        content_layout=values.get("content_layout", current.content_layout),
        completed_download_handling=values.get("completed_download_handling", current.completed_download_handling),
    )
    try:
        get_integration_config_store().save_download_client(updated, expected_revision=payload.expected_revision)
    except ValueError as exc:
        if str(exc) == "revision_conflict":
            raise AppError("REVISION_CONFLICT", "Download client changed; refresh and try again.", status_code=409) from exc
        raise
    client.health = "unknown"
    client.last_polled_at = None
    client.last_health_checked_at = None
    client.last_success_at = None
    client.latency_ms = None
    client.last_error = None
    await request_health_sweep(db)
    await db.commit()
    refreshed = await get_configured_download_client(db, client_id)
    assert refreshed is not None
    return _client_response(refreshed)


@router.delete("/download-clients/{client_id}", response_model=DeleteResponse)
async def delete_download_client(
    client_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    # Cascade removes only observation rows; durable Torrent history remains.
    get_integration_config_store().delete_download_client(client_id)
    await db.delete(client.state)
    await db.commit()
    return DeleteResponse(id=client_id)


@router.post("/download-clients/{client_id}/test", response_model=DownloadClientTestResponse)
async def test_download_client(
    client_id: UUID,
    payload: DownloadClientSavedTestRequest | None = None,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> DownloadClientTestResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    client.last_health_checked_at = utcnow()
    test_url = str(payload.url).rstrip("/") if payload and payload.url is not None else client.url
    test_username = payload.username if payload and payload.username is not None else (client.username or "")
    test_password = payload.password if payload and payload.password else (client.password or "")
    testing_saved_configuration = (
        test_url == client.url
        and test_username == (client.username or "")
        and test_password == (client.password or "")
    )
    try:
        started = perf_counter()
        adapter = client_factory(test_url, test_username, test_password)
        try:
            health = await adapter.health()
            result = {
                "status": "healthy",
                "version": health.get("version"),
                "latency_ms": round((perf_counter() - started) * 1000),
            }
        finally:
            await adapter.close()
    except Exception as exc:
        # Only persist health against the stored configuration. A test using
        # unsaved overrides is diagnostic and must not make the saved client
        # appear broken.
        if testing_saved_configuration:
            client.health = "unavailable"
            client.last_error = str(exc)
            client.latency_ms = None
        await db.commit()
        return DownloadClientTestResponse(status="unavailable", message=str(exc))
    if testing_saved_configuration:
        client.health = "healthy"
        client.last_success_at = utcnow()
        client.last_error = None
        client.latency_ms = int(result.get("latency_ms") or 0)
    await db.commit()
    return DownloadClientTestResponse(**result)


@router.get("/download-clients/{client_id}/health", response_model=DownloadClientTestResponse)
async def download_client_health(
    client_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DownloadClientTestResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    return DownloadClientTestResponse(status=client.health or "unknown")


@router.post("/download-clients/{client_id}/health/refresh", response_model=DownloadClientTestResponse)
async def refresh_download_client_health(
    client_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> DownloadClientTestResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    client.last_health_checked_at = utcnow()
    try:
        result = await test_download_client_connection(client, client_factory=client_factory)
    except Exception as exc:
        client.health = "unavailable"
        client.last_error = str(exc)
        client.latency_ms = None
        await db.commit()
        return DownloadClientTestResponse(status="unavailable", message=str(exc))
    client.health = "healthy"
    client.last_success_at = utcnow()
    client.last_error = None
    client.latency_ms = int(result.get("latency_ms") or 0)
    await db.commit()
    return DownloadClientTestResponse(**result)


@router.post("/download-clients/{client_id}/poll", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def poll_client(
    client_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> JobAcceptedResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    if not client.enabled:
        raise AppError("DOWNLOAD_CLIENT_DISABLED", "Download client is disabled.", status_code=409)
    job = await create_job(
        db,
        "qbittorrent_poll",
        summary={"client_id": str(client.id), "client_name": client.name, "message": f"Polling qBittorrent client {client.name}…"},
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_download_client_poll(job.id, client.id, client_factory=client_factory))
    return JobAcceptedResponse(job_id=job.id)


@router.post("/downloads/poll", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def poll_all_clients(
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> JobAcceptedResponse:
    del admin
    clients = [client for client in await list_configured_download_clients(db) if client.enabled]
    job = await create_job(
        db,
        "qbittorrent_poll_all",
        summary={"client_count": len(clients), "message": f"Polling {len(clients)} qBittorrent client{'s' if len(clients) != 1 else ''}…"},
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_all_download_client_polls(job.id, client_factory=client_factory))
    return JobAcceptedResponse(job_id=job.id)


@router.post("/downloads/actions", response_model=DownloadActionResponse)
async def apply_download_action(
    payload: DownloadActionRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> DownloadActionResponse:
    rows = (
        await db.execute(
            select(TorrentClientObservation, Torrent)
            .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
            .where(
                TorrentClientObservation.id.in_(payload.download_ids),
                TorrentClientObservation.is_present.is_(True),
            )
        )
    ).all()
    if not rows:
        raise AppError("NOT_FOUND", "No live downloads matched the request.", status_code=404)

    # An info hash only means something to the client holding it, so the batch
    # is split per client and each group is sent to its own qBittorrent.
    by_client: dict[UUID, list[str]] = {}
    for observation, torrent in rows:
        by_client.setdefault(observation.download_client_id, []).append(torrent.info_hash)

    applied = skipped = 0
    failures: list[DownloadActionFailure] = []
    for client_id, hashes in by_client.items():
        client = await get_configured_download_client(db, client_id)
        if client is None or not client.enabled:
            skipped += len(hashes)
            continue
        try:
            await run_torrent_action(client, payload.action, hashes, value=payload.value, client_factory=client_factory)
        except Exception as exc:
            # One unreachable client must not hide that the others succeeded.
            failures.append(
                DownloadActionFailure(client_id=client.id, client_name=client.name, count=len(hashes), error=str(exc))
            )
            continue
        applied += len(hashes)
        # qBittorrent applies most of these immediately, but not instantly; a
        # short pause makes the refreshed snapshot show the new state rather
        # than the one the action just replaced.
        await asyncio.sleep(0.35)
        await refresh_observations_after_action(db, client, hashes, client_factory=client_factory)
    if not applied:
        if failures:
            raise AppError(
                "UPSTREAM_ERROR",
                "; ".join(f"{item.client_name}: {item.error}" for item in failures),
                status_code=502,
            )
        raise AppError("CONFLICT", "No enabled download client owns the selected downloads.", status_code=409)
    return DownloadActionResponse(applied=applied, clients=len(by_client), skipped=skipped, failures=failures)


@router.get("/downloads/facets", response_model=DownloadFacetsResponse)
async def download_facets(
    client_id: UUID | None = None,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DownloadFacetsResponse:
    """Counts for every rail row, plus the category and tracker lists.

    One query returning a few narrow columns; the counting itself is trivial in
    Python and avoids a separate aggregate per row of the rail.
    """

    conditions = [TorrentClientObservation.is_present.is_(True), _non_historical_condition()]
    if client_id is not None:
        conditions.append(TorrentClientObservation.download_client_id == client_id)

    rows = (
        await db.execute(
            select(
                _activity_expression(),
                func.coalesce(TorrentClientObservation.progress, 0),
                func.coalesce(TorrentClientObservation.download_speed, 0),
                func.coalesce(TorrentClientObservation.upload_speed, 0),
                TorrentClientObservation.category,
                TorrentClientObservation.tags,
                Torrent.archive_state,
                _tracker_state_expression(),
                _tracker_host_expression(),
            )
            .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
            .where(*conditions)
        )
    ).all()

    counts: dict[str, int] = defaultdict(int)
    categories: set[str] = set()
    tag_names: set[str] = set()
    trackers: set[str] = set()
    for activity, progress, down, up, category, tags, archive_state, tracker_state, tracker_host in rows:
        counts["all"] += 1
        counts["category:all"] += 1
        counts["tag:all"] += 1
        counts["tracker:all"] += 1
        if activity in {"downloading", "metadata", "stalled"}:
            counts["downloading"] += 1
        if activity == "seeding":
            counts["seeding"] += 1
        if float(progress) >= 1:
            counts["completed"] += 1
        if activity in {"downloading", "metadata", "stalled", "checking"}:
            counts["running"] += 1
        if activity in {"paused", "queued"}:
            counts["stopped"] += 1
        if down > 0 or up > 0:
            counts["active"] += 1
        if activity == "stalled":
            counts["stalled"] += 1
        if activity == "checking":
            counts["checking"] += 1
        if activity == "moving":
            counts["moving"] += 1
        if activity == "error":
            counts["error"] += 1
        if activity == "error" or archive_state == TorrentArchiveState.FAILED or tracker_state == "unregistered":
            counts["attention"] += 1
        if archive_state == TorrentArchiveState.ARCHIVED:
            counts["backed-up"] += 1

        if category:
            categories.add(category)
            counts[f"category:{category}"] += 1
        else:
            counts["category:none"] += 1

        row_tags = [str(tag) for tag in (tags or []) if str(tag).strip()]
        if row_tags:
            for tag in row_tags:
                tag_names.add(tag)
                counts[f"tag:{tag}"] += 1
        else:
            counts["tag:none"] += 1

        host = _host_only(tracker_host)
        if host:
            trackers.add(host)
            counts[f"tracker:{host}"] += 1
        else:
            counts["tracker:none"] += 1
        if tracker_state in {"unregistered", "unavailable"}:
            counts["tracker:error"] += 1
        if tracker_state == "degraded":
            counts["tracker:warning"] += 1

    # Rows the UI always draws must report a number even when nothing matches.
    for key in (
        "all", "downloading", "seeding", "completed", "running", "stopped", "active",
        "stalled", "checking", "moving", "error", "attention", "backed-up",
        "category:all", "category:none", "tag:all", "tag:none", "tracker:all", "tracker:none",
        "tracker:error", "tracker:warning",
    ):
        counts.setdefault(key, 0)

    return DownloadFacetsResponse(
        counts=dict(counts),
        categories=sorted(categories, key=str.casefold),
        tags=sorted(tag_names, key=str.casefold),
        trackers=sorted(trackers, key=str.casefold),
    )


@router.get("/downloads", response_model=Collection[DownloadResponse])
async def list_downloads(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    client_id: UUID | None = None,
    scope: DownloadScope | None = None,
    state: str | None = None,
    filters: list[str] = Query(default_factory=list),
    sort: str | None = None,
    direction: str = Query("asc", pattern="^(asc|desc)$"),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[DownloadResponse]:
    conditions = []
    if client_id is not None:
        conditions.append(TorrentClientObservation.download_client_id == client_id)
    # Downloads is intentionally a live operational view. Removed observations
    # remain durable evidence elsewhere but never appear here.
    conditions.append(TorrentClientObservation.is_present.is_(True))
    # A historical association is preserved in release/torrent history, not in
    # the live activity workspace. Unassociated in-scope torrents remain visible
    # while Medialogue is still identifying/reconciling them.
    conditions.append(_non_historical_condition())
    if state:
        conditions.append(TorrentClientObservation.state == state)
    if scope:
        eligible_ids = [item.id for item in get_integration_config_store().list_download_clients() if item.scope == scope.value]
        if not eligible_ids:
            return Collection(items=[], page=page, page_size=page_size, total=0, pages=0)
        conditions.append(TorrentClientObservation.download_client_id.in_(eligible_ids))
    # Rail selections intersect: one per group, all of them narrowing together.
    for key in filters:
        condition = _filter_condition(key)
        if condition is not None:
            conditions.append(condition)

    base = (
        select(TorrentClientObservation, Torrent)
        .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
        .where(*conditions)
    )
    total = int(
        await db.scalar(
            select(func.count())
            .select_from(TorrentClientObservation)
            .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
            .where(*conditions)
        )
        or 0
    )

    order = _sort_expression(sort) if sort else None
    if order is None:
        order = TorrentClientObservation.last_seen_at.desc()
    else:
        order = order.desc() if direction == "desc" else order.asc()
    page_rows = (
        # The observation id breaks ties so equal sort values (cross-seeds with
        # one name, every torrent at 100%) keep a stable page across refreshes.
        await db.execute(
            base.order_by(order, TorrentClientObservation.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()

    # Cross-seeds sharing one exact qBittorrent content path are shown as one
    # payload group. Pull partners that sorted onto another page into this
    # one so a group is never split across pages.
    page_ids = {observation.id for observation, _torrent in page_rows}
    partner_paths = {
        observation.resolved_save_path
        for observation, torrent in page_rows
        if observation.resolved_save_path
        and str((torrent.metadata_json or {}).get("content_path") or "").strip()
    }
    if partner_paths:
        partners = (
            await db.execute(
                base.where(
                    TorrentClientObservation.resolved_save_path.in_(partner_paths),
                    TorrentClientObservation.id.not_in(page_ids),
                )
            )
        ).all()
        page_rows = [
            *page_rows,
            *(
                row
                for row in partners
                if str((row[1].metadata_json or {}).get("content_path") or "").strip()
            ),
        ]

    configured_by_id = {item.id: item for item in await list_configured_download_clients(db)}
    page_rows = [row for row in page_rows if row[0].download_client_id in configured_by_id]
    context = await _load_page_context(db, page_rows, configured_by_id)
    return Collection(
        items=[
            _download_response(observation, torrent, configured_by_id[observation.download_client_id], context)
            for observation, torrent in page_rows
        ],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )
