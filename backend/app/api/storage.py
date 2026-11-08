import os
import re
import string
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.integrations.filesystem import folder_writable
from app.models.auth import AdminUser
from app.models.domain import AccessMode, DiscoveryMode, Episode, EpisodeMediaMap, MediaDirectory, MediaFile, MediaType, Movie, MovieRelease, Problem, Season, RemotePathMapping, Show, ShowRelease, StorageRoot
from app.schemas.common import Collection, DeleteResponse
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.storage import (
    FilesystemEntry,
    FilesystemListing,
    LibrarySummary,
    RemotePathMappingCreate,
    RemotePathMappingResponse,
    RemotePathMappingUpdate,
    StorageRootCreate,
    StorageRootReorder,
    StorageRootResponse,
    StorageRootScan,
    StorageRootUpdate,
)
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job
from app.services.scheduler import request_health_sweep
from app.services.library_scan import active_storage_root_scan_job, run_storage_root_scan
from app.services.reconciliation import mark_root_available, mark_root_unavailable
from app.services.tmdb import get_tmdb_configuration

router = APIRouter(tags=["storage"])


def _resolved_path(path: str) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _normalized_mapping_prefix(value: str) -> str:
    """Store mapping prefixes in the same separator form used by qBit matching."""

    normalized = value.strip().replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized


def _remote_prefix_is_absolute(value: str) -> bool:
    return value.startswith("/") or re.match(r"^[A-Za-z]:/", value) is not None


def _validated_local_prefix(value: str) -> tuple[str, Path]:
    raw = value.strip()
    local = Path(raw).expanduser()
    if not local.is_absolute():
        raise AppError(
            "PATH_MAPPING_LOCAL_NOT_ABSOLUTE",
            "The Medialogue prefix must be an absolute path.",
            status_code=422,
        )
    resolved = local.resolve(strict=False)
    if not resolved.is_dir():
        raise AppError(
            "PATH_MAPPING_LOCAL_NOT_FOUND",
            f"The Medialogue prefix does not exist or is not a directory: {resolved}.",
            status_code=422,
        )
    return str(resolved), resolved


async def _reject_duplicate_mapping(
    db: AsyncSession,
    *,
    integration_type: object,
    integration_id: UUID | None,
    remote_prefix: str,
    exclude_id: UUID | None = None,
) -> None:
    query = select(RemotePathMapping).where(RemotePathMapping.integration_type == integration_type)
    query = query.where(
        RemotePathMapping.integration_id.is_(None)
        if integration_id is None
        else RemotePathMapping.integration_id == integration_id
    )
    if exclude_id is not None:
        query = query.where(RemotePathMapping.id != exclude_id)
    existing = (await db.scalars(query)).all()
    if any(_normalized_mapping_prefix(item.remote_prefix).casefold() == remote_prefix.casefold() for item in existing):
        raise AppError(
            "REMOTE_PATH_MAPPING_DUPLICATE",
            "A mapping for this client scope and remote prefix already exists.",
            status_code=409,
        )


def _reject_read_only_mount(path: str, access_mode: AccessMode) -> None:
    """A read/write media folder must really be writable where Medialogue sees it."""

    if access_mode == AccessMode.READ_WRITE and Path(path).is_dir() and not folder_writable(path):
        raise AppError(
            "MEDIA_FOLDER_NOT_WRITABLE",
            f"{path} is mounted read-only, so Medialogue could not delete or clean up media there. "
            "Mount it writable, or set this media folder to Read only.",
            status_code=409,
        )


async def _root_response(db: AsyncSession, root: StorageRoot) -> StorageRootResponse:
    media_count = int(
        await db.scalar(
            select(func.count()).select_from(MediaDirectory).where(MediaDirectory.storage_root_id == root.id)
        )
        or 0
    )
    affected = media_count if root.last_health == "unavailable" else 0
    missing_media = int(
        await db.scalar(
            select(func.count())
            .select_from(MediaDirectory)
            .where(MediaDirectory.storage_root_id == root.id, MediaDirectory.exists.is_(False))
        )
        or 0
    )
    job = await active_storage_root_scan_job(db, root.id)
    scan = None
    if job is not None:
        progress = job.progress or {}
        scan = StorageRootScan(
            job_id=job.id,
            status=job.status.value if hasattr(job.status, "value") else str(job.status),
            stage=progress.get("stage"),
            current=int(progress.get("current") or 0),
            total=int(progress.get("total") or 0),
            percent=float(progress.get("percent") or 0),
        )
    return StorageRootResponse.model_validate(root).model_copy(
        update={
            "affected_media_count": affected,
            "missing_media_count": missing_media,
            "media_count": media_count,
            "scanning": job is not None,
            "scan": scan,
        }
    )


def _path_inside(path: str, root: str) -> bool:
    """Whether a recorded path sits inside a root, on this platform.

    Not path_mapping.is_inside_root: that one is deliberately PurePosixPath,
    because it compares container paths. Media folder paths are whatever the
    host uses, drive letters and UNC shares on Windows, so this comparison
    has to be platform-aware.
    """
    try:
        Path(path).resolve(strict=False).relative_to(Path(root).resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False


async def _adopt_unlinked_directories(db: AsyncSession, root: StorageRoot) -> int:
    """Re-link directories that already live inside a newly added root.

    Removing a root detaches its directories rather than deleting them, so
    adding the same folder back has to reconnect them, otherwise removal is
    quietly irreversible. This also covers splitting an existing root: point a
    new one at a subfolder and what is already recorded there attaches to it.

    Only unlinked directories are adopted. A directory belonging to another
    root is never taken from it.
    """
    orphans = (
        await db.scalars(select(MediaDirectory).where(MediaDirectory.storage_root_id.is_(None)))
    ).all()
    adopted = 0
    for directory in orphans:
        if _path_inside(directory.resolved_path, root.resolved_root_path):
            directory.storage_root_id = root.id
            adopted += 1
    if adopted:
        await db.commit()
    return adopted


@router.get("/storage-roots", response_model=Collection[StorageRootResponse])
async def list_storage_roots(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[StorageRootResponse]:
    del admin
    total = await db.scalar(select(func.count()).select_from(StorageRoot)) or 0
    rows = (await db.scalars(select(StorageRoot).order_by(StorageRoot.sort_order, StorageRoot.name).offset((page - 1) * page_size).limit(page_size))).all()
    pages = (total + page_size - 1) // page_size
    return Collection(items=[await _root_response(db, row) for row in rows], page=page, page_size=page_size, total=total, pages=pages)


@router.get("/library/summary", response_model=LibrarySummary)
async def library_summary(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LibrarySummary:
    """What is in the library, and how much of it is no longer linked to a root.

    Nothing else in the system looks for unlinked directories, so without this
    they are invisible: removing a root leaves entries that are never scanned,
    never checked, and never mentioned anywhere.
    """
    return LibrarySummary(
        movies=int(await db.scalar(select(func.count()).select_from(Movie)) or 0),
        shows=int(await db.scalar(select(func.count()).select_from(Show)) or 0),
        unlinked_directories=int(
            await db.scalar(
                select(func.count()).select_from(MediaDirectory).where(MediaDirectory.storage_root_id.is_(None))
            )
            or 0
        ),
    )


@router.post("/storage-roots/health", response_model=Collection[StorageRootResponse])
async def check_storage_root_health(
    _: object = Depends(require_csrf),
    __: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[StorageRootResponse]:
    """Re-test every root's path and record what was found.

    Health was previously written only by a scan, so a root that went offline
    stayed green until something scanned it again. This is a directory stat per
    root, cheap enough to run whenever the page is opened.
    """
    roots = (await db.scalars(select(StorageRoot).order_by(StorageRoot.sort_order, StorageRoot.name))).all()
    for root in roots:
        if not root.enabled:
            continue
        reachable = False
        try:
            reachable = Path(root.resolved_root_path).is_dir()
        except OSError:
            reachable = False
        if reachable:
            await mark_root_available(db, root)
        else:
            await mark_root_unavailable(db, root)
    await db.commit()
    items = [await _root_response(db, root) for root in roots]
    return Collection(items=items, page=1, page_size=len(items) or 1, total=len(items), pages=1)


@router.get("/filesystem", response_model=FilesystemListing)
async def browse_filesystem(
    path: str = Query("", max_length=4096),
    _: AdminUser = Depends(require_admin),
) -> FilesystemListing:
    """List the folders inside a path, so a root can be picked by browsing.

    Only directories are returned: choosing a root never involves a file, and
    listing them would expose more than the task needs. Nothing is read from
    inside the folders and nothing is written.
    """
    target = path.strip()
    if not target:
        return FilesystemListing(current_path="", parent=None, directories=_mount_points())

    raw = Path(target)
    if not raw.is_absolute():
        return FilesystemListing(current_path=target, parent=None, problem="Enter an absolute path.")
    try:
        resolved = raw.resolve()
    except OSError:
        resolved = raw

    if resolved.parent == resolved:
        parent = "" if os.name == "nt" else None
    else:
        parent = str(resolved.parent)
    if not resolved.exists():
        return FilesystemListing(current_path=str(resolved), parent=parent, problem="This folder does not exist.")
    if not resolved.is_dir():
        return FilesystemListing(current_path=str(resolved), parent=parent, problem="This is a file, not a folder.")

    try:
        entries = [
            FilesystemEntry(name=entry.name, path=str(entry))
            for entry in sorted(resolved.iterdir(), key=lambda item: item.name.casefold())
            if entry.is_dir()
        ]
    except PermissionError:
        return FilesystemListing(current_path=str(resolved), parent=parent, problem="Medialogue is not allowed to read this folder.")
    except OSError as exc:
        return FilesystemListing(current_path=str(resolved), parent=parent, problem=f"This folder could not be read: {exc.strerror or exc}")

    return FilesystemListing(current_path=str(resolved), parent=parent, directories=entries)


def _mount_points() -> list[FilesystemEntry]:
    """The top of the tree: drive letters on Windows, / everywhere else."""
    if os.name != "nt":
        return [FilesystemEntry(name="/", path="/")]
    drives: list[FilesystemEntry] = []
    for letter in string.ascii_uppercase:
        candidate = Path(f"{letter}:" + os.sep)
        try:
            if candidate.exists():
                drives.append(FilesystemEntry(name=f"{letter}:", path=str(candidate)))
        except OSError:
            continue
    return drives


@router.post("/storage-roots", response_model=StorageRootResponse, status_code=status.HTTP_201_CREATED)
async def create_storage_root(
    payload: StorageRootCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> StorageRootResponse:
    _reject_read_only_mount(_resolved_path(payload.path), AccessMode(payload.access_mode.value))
    root = StorageRoot(
        name=payload.name,
        resolved_root_path=_resolved_path(payload.path),
        media_type=MediaType(payload.media_type.value),
        access_mode=AccessMode(payload.access_mode.value),
        discovery_mode=DiscoveryMode(payload.discovery_mode.value),
        enabled=payload.enabled,
        missing_grace_checks=payload.missing_grace_checks,
        last_health="unavailable" if not Path(payload.path).exists() else "available",
        sort_order=int(await db.scalar(select(func.max(StorageRoot.sort_order))) or 0) + 1,
    )
    db.add(root)
    await request_health_sweep(db)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError("STORAGE_ROOT_EXISTS", "A media folder with this name already exists.", status_code=409) from exc
    await _adopt_unlinked_directories(db, root)
    return await _root_response(db, root)


@router.post("/storage-roots/reorder", response_model=Collection[StorageRootResponse])
async def reorder_storage_roots(
    payload: StorageRootReorder,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> Collection[StorageRootResponse]:
    """Persist a hand-dragged list order.

    Ids missing from the payload keep a position after the ones given, so a
    reorder raced against another admin's newly added root cannot silently
    drop that root to an arbitrary place.
    """
    roots = (await db.scalars(select(StorageRoot))).all()
    by_id = {root.id: root for root in roots}
    unknown = [str(root_id) for root_id in payload.ids if root_id not in by_id]
    if unknown:
        raise AppError("STORAGE_ROOT_NOT_FOUND", f"Unknown media folder: {', '.join(unknown)}", status_code=404)

    ordered = [by_id[root_id] for root_id in payload.ids]
    ordered += [root for root in sorted(roots, key=lambda r: (r.sort_order, r.name)) if root.id not in set(payload.ids)]
    for position, root in enumerate(ordered):
        root.sort_order = position
    await db.commit()
    return Collection(
        items=[await _root_response(db, root) for root in ordered],
        page=1,
        page_size=len(ordered),
        total=len(ordered),
        pages=1,
    )


@router.get("/storage-roots/{root_id}", response_model=StorageRootResponse)
async def get_storage_root(root_id: UUID, _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> StorageRootResponse:
    root = await db.get(StorageRoot, root_id)
    if root is None:
        raise AppError("NOT_FOUND", "Media folder was not found.", status_code=404)
    return await _root_response(db, root)


@router.patch("/storage-roots/{root_id}", response_model=StorageRootResponse)
async def update_storage_root(
    root_id: UUID,
    payload: StorageRootUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> StorageRootResponse:
    root = await db.get(StorageRoot, root_id)
    if root is None:
        raise AppError("NOT_FOUND", "Media folder was not found.", status_code=404)
    values = payload.model_dump(exclude_unset=True)
    if "path" in values:
        values["resolved_root_path"] = _resolved_path(values.pop("path"))
    if "media_type" in values:
        values["media_type"] = MediaType(values["media_type"])
    if "access_mode" in values:
        values["access_mode"] = AccessMode(values["access_mode"])
    if "discovery_mode" in values:
        values["discovery_mode"] = DiscoveryMode(values["discovery_mode"])
    scan_identity_changed = (
        values.get("resolved_root_path", root.resolved_root_path) != root.resolved_root_path
        or values.get("media_type", root.media_type) != root.media_type
    )
    if scan_identity_changed and await active_storage_root_scan_job(db, root.id) is not None:
        raise AppError(
            "STORAGE_ROOT_SCANNING",
            "Wait for the current scan to finish before changing this root's path or media type.",
            status_code=409,
        )
    _reject_read_only_mount(values.get("resolved_root_path", root.resolved_root_path),
                            values.get("access_mode", root.access_mode))
    for key, value in values.items():
        setattr(root, key, value)
    if scan_identity_changed:
        root.last_scan_at = None
    await request_health_sweep(db)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError("STORAGE_ROOT_EXISTS", "A media folder with this name already exists.", status_code=409) from exc
    return await _root_response(db, root)


async def _show_ids_in(db: AsyncSession, condition) -> set[UUID]:
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


async def _movie_ids_in(db: AsyncSession, condition) -> set[UUID]:
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


async def _remove_entries_under(db: AsyncSession, root: StorageRoot) -> None:
    """Delete the movies and shows recorded under one root.

    Only entries that live *entirely* under this root are removed. Something
    with directories under another root as well survives, because removing this
    root does not make it unknown, it only makes part of it unreachable.

    Files are never touched. These rows are derived from a scan and a scan
    rebuilds them.
    """
    here = MediaDirectory.storage_root_id == root.id
    elsewhere = and_(
        MediaDirectory.storage_root_id.is_not(None),
        MediaDirectory.storage_root_id != root.id,
    )

    movie_ids = await _movie_ids_in(db, here) - await _movie_ids_in(db, elsewhere)
    show_ids = await _show_ids_in(db, here) - await _show_ids_in(db, elsewhere)
    movie_release_ids = set(
        (await db.scalars(select(MovieRelease.id).where(MovieRelease.movie_id.in_(movie_ids)))).all()
    ) if movie_ids else set()
    show_release_ids = set(
        (await db.scalars(select(ShowRelease.id).where(ShowRelease.show_id.in_(show_ids)))).all()
    ) if show_ids else set()
    episode_ids = set(
        (await db.scalars(select(Episode.id).where(Episode.show_id.in_(show_ids)))).all()
    ) if show_ids else set()
    directory_ids = set((await db.scalars(select(MediaDirectory.id).where(here))).all())
    media_file_ids = set(
        (await db.scalars(select(MediaFile.id).where(MediaFile.media_directory_id.in_(directory_ids)))).all()
    ) if directory_ids else set()

    problem_subjects = {
        "movie": movie_ids,
        "movie_release": movie_release_ids,
        "show": show_ids,
        "show_release": show_release_ids,
        "episode": episode_ids,
        "media_directory": directory_ids,
        "media_file": media_file_ids,
    }
    problem_conditions = [
        and_(Problem.entity_type == entity_type, Problem.entity_id.in_(entity_ids))
        for entity_type, entity_ids in problem_subjects.items()
        if entity_ids
    ]
    if problem_conditions:
        # Problem.entity_id is deliberately polymorphic and therefore has no
        # foreign key. Delete its rows explicitly before their subjects vanish.
        await db.execute(delete(Problem).where(or_(*problem_conditions)))

    for owner_id in movie_ids:
        row = await db.get(Movie, owner_id)
        if row is not None:
            await db.delete(row)

    for owner_id in show_ids:
        row = await db.get(Show, owner_id)
        if row is not None:
            await db.delete(row)

    directories = (await db.scalars(select(MediaDirectory).where(here))).all()
    for directory in directories:
        await db.delete(directory)
    await db.flush()


@router.delete("/storage-roots/{root_id}", response_model=DeleteResponse)
async def delete_storage_root(
    root_id: UUID,
    remove_entries: bool = Query(False),
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    root = await db.get(StorageRoot, root_id)
    if root is None:
        raise AppError("NOT_FOUND", "Media folder was not found.", status_code=404)
    if await active_storage_root_scan_job(db, root.id) is not None:
        raise AppError(
            "STORAGE_ROOT_SCANNING",
            "This root is being scanned. Wait for the scan to finish, or cancel it in Jobs.",
            status_code=409,
        )
    if remove_entries:
        await _remove_entries_under(db, root)
    # Root Problems are polymorphic too, so deleting the configuration cannot
    # cascade to them. A removed root can no longer have an actionable warning.
    await db.execute(
        delete(Problem).where(Problem.entity_type == "storage_root", Problem.entity_id == root.id)
    )
    # Removing a configured root never removes media files. Any directory left
    # is detached rather than deleted, so its observed path stays available.
    await db.execute(
        update(MediaDirectory)
        .where(MediaDirectory.storage_root_id == root.id)
        .values(storage_root_id=None)
    )
    await db.execute(
        update(RemotePathMapping)
        .where(RemotePathMapping.storage_root_id == root.id)
        .values(storage_root_id=None)
    )
    await db.delete(root)
    await db.commit()
    return DeleteResponse(id=root_id)


@router.post("/storage-roots/{root_id}/scan", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def scan_storage_root(
    root_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedResponse:
    root = await db.get(StorageRoot, root_id)
    if root is None:
        raise AppError("NOT_FOUND", "Media folder was not found.", status_code=404)
    if not root.enabled:
        raise AppError("STORAGE_ROOT_DISABLED", "Media folder is disabled.", status_code=409)
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError(
            "TMDB_NOT_CONFIGURED",
            "Configure TMDB before scanning. Medialogue cannot identify discovered media without it.",
            status_code=409,
        )
    existing = await active_storage_root_scan_job(db, root.id)
    if existing is not None:
        return JobAcceptedResponse(job_id=existing.id)
    job = await create_job(db, "storage_root_scan", summary={"storage_root_id": str(root.id), "path": root.resolved_root_path})
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_storage_root_scan(job.id, root.id))
    return JobAcceptedResponse(job_id=job.id)


@router.get("/remote-path-mappings", response_model=Collection[RemotePathMappingResponse])
async def list_mappings(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[RemotePathMappingResponse]:
    total = await db.scalar(select(func.count()).select_from(RemotePathMapping)) or 0
    rows = (await db.scalars(select(RemotePathMapping).order_by(RemotePathMapping.name).offset((page - 1) * page_size).limit(page_size))).all()
    pages = (total + page_size - 1) // page_size
    return Collection(items=[RemotePathMappingResponse.model_validate(row) for row in rows], page=page, page_size=page_size, total=total, pages=pages)


@router.post("/remote-path-mappings", response_model=RemotePathMappingResponse, status_code=status.HTTP_201_CREATED)
async def create_mapping(payload: RemotePathMappingCreate, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> RemotePathMappingResponse:
    values = payload.model_dump()
    remote_prefix = _normalized_mapping_prefix(payload.remote_prefix)
    if not _remote_prefix_is_absolute(remote_prefix):
        raise AppError(
            "PATH_MAPPING_REMOTE_NOT_ABSOLUTE",
            "The remote prefix must be an absolute POSIX, Windows, or UNC path.",
            status_code=422,
        )
    values["remote_prefix"] = remote_prefix
    local_prefix, local_path = _validated_local_prefix(payload.local_prefix)
    values["local_prefix"] = local_prefix
    if payload.storage_root_id is not None:
        root = await db.get(StorageRoot, payload.storage_root_id)
        if root is None:
            raise AppError("STORAGE_ROOT_NOT_FOUND", "The selected media folder was not found.", status_code=404)
        root_path = Path(root.resolved_root_path).expanduser().resolve(strict=False)
        try:
            local_path.relative_to(root_path)
        except ValueError as exc:
            raise AppError(
                "PATH_MAPPING_OUTSIDE_ROOT",
                (
                    "A mapping assigned to a media folder must translate into that folder. "
                    f"Local prefix {local_path} is outside {root_path}."
                ),
                status_code=422,
            ) from exc
    await _reject_duplicate_mapping(
        db,
        integration_type=payload.integration_type,
        integration_id=payload.integration_id,
        remote_prefix=remote_prefix,
    )
    mapping = RemotePathMapping(**values)
    db.add(mapping)
    await db.commit()
    return RemotePathMappingResponse.model_validate(mapping)


@router.patch("/remote-path-mappings/{mapping_id}", response_model=RemotePathMappingResponse)
async def update_mapping(
    mapping_id: UUID,
    payload: RemotePathMappingUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> RemotePathMappingResponse:
    mapping = await db.get(RemotePathMapping, mapping_id)
    if mapping is None:
        raise AppError("NOT_FOUND", "Remote path mapping was not found.", status_code=404)
    values = payload.model_dump(exclude_unset=True)
    target_remote_prefix = _normalized_mapping_prefix(values.get("remote_prefix", mapping.remote_prefix))
    if not _remote_prefix_is_absolute(target_remote_prefix):
        raise AppError(
            "PATH_MAPPING_REMOTE_NOT_ABSOLUTE",
            "The remote prefix must be an absolute POSIX, Windows, or UNC path.",
            status_code=422,
        )
    values["remote_prefix"] = target_remote_prefix
    target_root_id = values.get("storage_root_id", mapping.storage_root_id)
    target_local_prefix = values.get("local_prefix", mapping.local_prefix)
    local_prefix, local_path = _validated_local_prefix(target_local_prefix)
    values["local_prefix"] = local_prefix
    if target_root_id is not None:
        root = await db.get(StorageRoot, target_root_id)
        if root is None:
            raise AppError("STORAGE_ROOT_NOT_FOUND", "The selected media folder was not found.", status_code=404)
        root_path = Path(root.resolved_root_path).expanduser().resolve(strict=False)
        try:
            local_path.relative_to(root_path)
        except ValueError as exc:
            raise AppError(
                "PATH_MAPPING_OUTSIDE_ROOT",
                f"A mapping assigned to a media folder must translate into that folder. Local prefix {local_path} is outside {root_path}.",
                status_code=422,
            ) from exc
    await _reject_duplicate_mapping(
        db,
        integration_type=values.get("integration_type", mapping.integration_type),
        integration_id=values.get("integration_id", mapping.integration_id),
        remote_prefix=target_remote_prefix,
        exclude_id=mapping.id,
    )
    for key, value in values.items():
        setattr(mapping, key, value)
    await db.commit()
    return RemotePathMappingResponse.model_validate(mapping)


@router.delete("/remote-path-mappings/{mapping_id}", response_model=DeleteResponse)
async def delete_mapping(mapping_id: UUID, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> DeleteResponse:
    mapping = await db.get(RemotePathMapping, mapping_id)
    if mapping is None:
        raise AppError("NOT_FOUND", "Remote path mapping was not found.", status_code=404)
    await db.delete(mapping)
    await db.commit()
    return DeleteResponse(id=mapping_id)
