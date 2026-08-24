import os
import string
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import (
    AccessMode,
    Job,
    MediaDirectory,
    MediaType,
    Movie,
    MovieRelease,
    RemotePathMapping,
    Show,
    ShowRelease,
    StorageRoot,
)
from app.schemas.common import Collection, DeleteResponse
from app.schemas.jobs import JobAcceptedResponse, JobResponse
from app.schemas.storage import (
    FilesystemEntry,
    FilesystemListing,
    LibrarySummary,
    RemotePathMappingCreate,
    RemotePathMappingResponse,
    RemotePathMappingUpdate,
    StorageRootCreate,
    StorageRootResponse,
    StorageRootUpdate,
)
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job
from app.services.library_scan import active_storage_root_scan_job, run_storage_root_scan
from app.services.reconciliation import mark_root_available, mark_root_unavailable
from app.services.tmdb import get_tmdb_configuration

router = APIRouter(tags=["storage"])


def _resolved_path(path: str) -> str:
    # resolve(strict=False) canonicalizes `..` but does not create a directory
    # or require a currently mounted NAS path.
    return str(Path(path).expanduser().resolve(strict=False))


async def _root_response(db: AsyncSession, root: StorageRoot) -> StorageRootResponse:
    media_count = int(
        await db.scalar(
            select(func.count()).select_from(MediaDirectory).where(MediaDirectory.storage_root_id == root.id)
        )
        or 0
    )
    affected = media_count if root.last_health == "unavailable" else 0
    scanning = await active_storage_root_scan_job(db, root.id) is not None
    return StorageRootResponse.model_validate(root).model_copy(
        update={"affected_media_count": affected, "media_count": media_count, "scanning": scanning}
    )


def _path_inside(path: str, root: str) -> bool:
    """Whether a recorded path sits inside a root, on this platform.

    Not path_mapping.is_inside_root: that one is deliberately PurePosixPath,
    because it compares container paths. Root storage paths are whatever the
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
    rows = (await db.scalars(select(StorageRoot).order_by(StorageRoot.name).offset((page - 1) * page_size).limit(page_size))).all()
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
    roots = (await db.scalars(select(StorageRoot).order_by(StorageRoot.name))).all()
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
    root = StorageRoot(
        name=payload.name,
        resolved_root_path=_resolved_path(payload.path),
        media_type=MediaType(payload.media_type.value),
        access_mode=AccessMode(payload.access_mode.value),
        enabled=payload.enabled,
        missing_grace_checks=payload.missing_grace_checks,
        last_health="unavailable" if not Path(payload.path).exists() else "available",
    )
    db.add(root)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise AppError("STORAGE_ROOT_EXISTS", "A storage root with this name already exists.", status_code=409) from exc
    await _adopt_unlinked_directories(db, root)
    return await _root_response(db, root)


@router.get("/storage-roots/{root_id}", response_model=StorageRootResponse)
async def get_storage_root(root_id: UUID, _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> StorageRootResponse:
    root = await db.get(StorageRoot, root_id)
    if root is None:
        raise AppError("NOT_FOUND", "Storage root was not found.", status_code=404)
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
        raise AppError("NOT_FOUND", "Storage root was not found.", status_code=404)
    values = payload.model_dump(exclude_unset=True)
    if "path" in values:
        values["resolved_root_path"] = _resolved_path(values.pop("path"))
    if "media_type" in values:
        values["media_type"] = MediaType(values["media_type"])
    if "access_mode" in values:
        values["access_mode"] = AccessMode(values["access_mode"])
    for key, value in values.items():
        setattr(root, key, value)
    await db.commit()
    return await _root_response(db, root)


async def _remove_entries_under(db: AsyncSession, root: StorageRoot) -> None:
    """Delete the movies and shows recorded under one root.

    Only entries that live *entirely* under this root are removed. Something
    with directories under another root as well survives, because removing this
    root does not make it unknown, it only makes part of it unreachable.

    Files are never touched. These rows are derived from a scan and a scan
    rebuilds them.
    """
    directories = (
        await db.scalars(select(MediaDirectory).where(MediaDirectory.storage_root_id == root.id))
    ).all()

    movie_ids: set[UUID] = set()
    show_ids: set[UUID] = set()
    for directory in directories:
        if directory.movie_release_id:
            release = await db.get(MovieRelease, directory.movie_release_id)
            if release:
                movie_ids.add(release.movie_id)
        if directory.show_release_id:
            release = await db.get(ShowRelease, directory.show_release_id)
            if release:
                show_ids.add(release.show_id)

    async def survives_elsewhere(release_model, owner_column, link_column, owner_id: UUID) -> bool:
        """Whether this movie/show still has a directory under another root."""
        release_ids = (await db.scalars(select(release_model.id).where(owner_column == owner_id))).all()
        if not release_ids:
            return False
        remaining = await db.scalar(
            select(func.count())
            .select_from(MediaDirectory)
            .where(
                link_column.in_(release_ids),
                MediaDirectory.storage_root_id.is_not(None),
                MediaDirectory.storage_root_id != root.id,
            )
        )
        return bool(int(remaining or 0))

    for owner_id in movie_ids:
        if await survives_elsewhere(MovieRelease, MovieRelease.movie_id, MediaDirectory.movie_release_id, owner_id):
            continue
        row = await db.get(Movie, owner_id)
        if row is not None:
            await db.delete(row)

    for owner_id in show_ids:
        if await survives_elsewhere(ShowRelease, ShowRelease.show_id, MediaDirectory.show_release_id, owner_id):
            continue
        row = await db.get(Show, owner_id)
        if row is not None:
            await db.delete(row)

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
        raise AppError("NOT_FOUND", "Storage root was not found.", status_code=404)
    if await active_storage_root_scan_job(db, root.id) is not None:
        raise AppError(
            "STORAGE_ROOT_SCANNING",
            "This root is being scanned. Wait for the scan to finish, or cancel it in Jobs.",
            status_code=409,
        )
    if remove_entries:
        await _remove_entries_under(db, root)
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
        raise AppError("NOT_FOUND", "Storage root was not found.", status_code=404)
    if not root.enabled:
        raise AppError("STORAGE_ROOT_DISABLED", "Storage root is disabled.", status_code=409)
    # TMDB establishes the identity of everything a scan discovers. Without it,
    # every directory would be recorded as an unidentified Problem, one global
    # misconfiguration turning into thousands of individually actionable rows.
    # Refusing to start is the honest answer: fix the one setting, then scan.
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
    if payload.storage_root_id is not None:
        root = await db.get(StorageRoot, payload.storage_root_id)
        if root is None:
            raise AppError("STORAGE_ROOT_NOT_FOUND", "The selected storage root was not found.", status_code=404)
        local_prefix = Path(payload.local_prefix).expanduser().resolve(strict=False)
        root_path = Path(root.resolved_root_path).expanduser().resolve(strict=False)
        try:
            local_prefix.relative_to(root_path)
        except ValueError as exc:
            raise AppError(
                "PATH_MAPPING_OUTSIDE_ROOT",
                (
                    "A mapping assigned to a storage root must translate into that root. "
                    f"Local prefix {local_prefix} is outside {root_path}."
                ),
                status_code=422,
            ) from exc
        values["local_prefix"] = str(local_prefix)
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
    target_root_id = values.get("storage_root_id", mapping.storage_root_id)
    target_local_prefix = values.get("local_prefix", mapping.local_prefix)
    if target_root_id is not None:
        root = await db.get(StorageRoot, target_root_id)
        if root is None:
            raise AppError("STORAGE_ROOT_NOT_FOUND", "The selected storage root was not found.", status_code=404)
        local_prefix = Path(target_local_prefix).expanduser().resolve(strict=False)
        root_path = Path(root.resolved_root_path).expanduser().resolve(strict=False)
        try:
            local_prefix.relative_to(root_path)
        except ValueError as exc:
            raise AppError(
                "PATH_MAPPING_OUTSIDE_ROOT",
                f"A mapping assigned to a storage root must translate into that root. Local prefix {local_prefix} is outside {root_path}.",
                status_code=422,
            ) from exc
        values["local_prefix"] = str(local_prefix)
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
