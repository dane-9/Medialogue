from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Library, MediaType, StorageRoot
from app.schemas.common import DeleteResponse
from app.schemas.libraries import LibraryCreate, LibraryFolders, LibraryReorder, LibraryResponse, LibraryUpdate
from app.services.jobs import create_job, publish_job_status
from app.services.libraries import assign_root, get_library, library_folder_count, library_title_count
from app.services.library_scan import active_storage_root_scan_job, run_storage_root_scan
from app.services.runtime_jobs import launch_runtime_job
from app.services.tmdb import get_tmdb_configuration

router = APIRouter(tags=["libraries"])


class LibraryScanResponse(BaseModel):
    job_ids: list[UUID]


async def _response(db: AsyncSession, library: Library) -> LibraryResponse:
    return LibraryResponse.model_validate(library).model_copy(
        update={
            "folder_count": await library_folder_count(db, library.id),
            "title_count": await library_title_count(db, library),
        }
    )


async def _ordered(db: AsyncSession) -> list[Library]:
    return list((await db.scalars(select(Library).order_by(Library.sort_order, Library.name))).all())


async def _commit_named(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError("LIBRARY_EXISTS", "A library with this name already exists.", status_code=409) from exc


@router.get("/libraries", response_model=list[LibraryResponse])
async def list_libraries(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[LibraryResponse]:
    return [await _response(db, library) for library in await _ordered(db)]


@router.post("/libraries", response_model=LibraryResponse, status_code=status.HTTP_201_CREATED)
async def create_library(
    payload: LibraryCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> LibraryResponse:
    library = Library(
        name=payload.name,
        kind=MediaType(payload.kind.value),
        sort_order=int(await db.scalar(select(func.max(Library.sort_order))) or 0) + 1,
    )
    db.add(library)
    await _commit_named(db)
    return await _response(db, library)


@router.post("/libraries/reorder", response_model=list[LibraryResponse])
async def reorder_libraries(
    payload: LibraryReorder,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> list[LibraryResponse]:
    """Persist a hand-dragged sidebar order. Libraries left out keep their
    relative order after the ones given."""

    libraries = await _ordered(db)
    by_id = {library.id: library for library in libraries}
    unknown = [str(library_id) for library_id in payload.ids if library_id not in by_id]
    if unknown:
        raise AppError("LIBRARY_NOT_FOUND", f"Unknown library: {', '.join(unknown)}", status_code=404)
    given = list(dict.fromkeys(payload.ids))
    ordered = [by_id[library_id] for library_id in given] + [item for item in libraries if item.id not in set(given)]
    for position, library in enumerate(ordered):
        library.sort_order = position
    await db.commit()
    return [await _response(db, library) for library in ordered]


@router.get("/libraries/{library_id}", response_model=LibraryResponse)
async def get_library_detail(
    library_id: UUID, _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> LibraryResponse:
    return await _response(db, await get_library(db, library_id))


@router.patch("/libraries/{library_id}", response_model=LibraryResponse)
async def update_library(
    library_id: UUID,
    payload: LibraryUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> LibraryResponse:
    library = await get_library(db, library_id)
    if payload.name is not None:
        library.name = payload.name
    if payload.kind is not None and MediaType(payload.kind.value) != library.kind:
        if await library_folder_count(db, library.id) or await library_title_count(db, library):
            raise AppError(
                "LIBRARY_NOT_EMPTY",
                f"{library.name} has media folders or titles, so its type cannot change.",
                status_code=409,
            )
        library.kind = MediaType(payload.kind.value)
    await _commit_named(db)
    return await _response(db, library)


@router.delete("/libraries/{library_id}", response_model=DeleteResponse)
async def delete_library(
    library_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    library = await get_library(db, library_id)
    folders = await library_folder_count(db, library.id)
    titles = await library_title_count(db, library)
    if folders or titles:
        parts = [f"{count} {noun}{'' if count == 1 else 's'}" for count, noun in ((folders, "media folder"), (titles, "title")) if count]
        raise AppError(
            "LIBRARY_NOT_EMPTY",
            f"{library.name} still has {' and '.join(parts)}. Move or remove them first.",
            status_code=409,
        )
    await db.delete(library)
    await db.commit()
    return DeleteResponse(id=library_id)


@router.put("/libraries/{library_id}/folders", response_model=LibraryResponse)
async def set_library_folders(
    library_id: UUID,
    payload: LibraryFolders,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> LibraryResponse:
    """Make exactly these media folders the library's. All or nothing."""

    library = await get_library(db, library_id)
    wanted = set(payload.storage_root_ids)
    roots = {root.id: root for root in (await db.scalars(select(StorageRoot).where(StorageRoot.id.in_(wanted)))).all()}
    unknown = [str(root_id) for root_id in wanted if root_id not in roots]
    if unknown:
        raise AppError("STORAGE_ROOT_NOT_FOUND", f"Unknown media folder: {', '.join(unknown)}", status_code=404)
    current = (await db.scalars(select(StorageRoot).where(StorageRoot.library_id == library.id))).all()
    changing = [root for root in current if root.id not in wanted] + [root for root in roots.values() if root.library_id != library.id]
    for root in changing:
        if await active_storage_root_scan_job(db, root.id) is not None:
            raise AppError(
                "STORAGE_ROOT_SCANNING",
                f"{root.name} is being scanned. Wait for the scan to finish before moving it.",
                status_code=409,
            )
    try:
        for root in current:
            if root.id not in wanted:
                await assign_root(db, root, None)
        for root in roots.values():
            await assign_root(db, root, library)
    except AppError:
        await db.rollback()
        raise
    await db.commit()
    return await _response(db, library)


@router.post("/libraries/{library_id}/scan", response_model=LibraryScanResponse, status_code=status.HTTP_202_ACCEPTED)
async def scan_library(
    library_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> LibraryScanResponse:
    """Scan every enabled media folder in the library."""

    library = await get_library(db, library_id)
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError(
            "TMDB_NOT_CONFIGURED",
            "Configure TMDB before scanning. Medialogue cannot identify discovered media without it.",
            status_code=409,
        )
    roots = (
        await db.scalars(
            select(StorageRoot)
            .where(StorageRoot.library_id == library.id, StorageRoot.active)
            .order_by(StorageRoot.sort_order, StorageRoot.name)
        )
    ).all()
    job_ids: list[UUID] = []
    started = []
    for root in roots:
        existing = await active_storage_root_scan_job(db, root.id)
        if existing is not None:
            job_ids.append(existing.id)
            continue
        job = await create_job(db, "storage_root_scan", summary={"storage_root_id": str(root.id), "path": root.resolved_root_path})
        job_ids.append(job.id)
        started.append((job, root.id))
    await db.commit()
    for job, root_id in started:
        publish_job_status(job)
        launch_runtime_job(job.id, lambda job_id=job.id, root_id=root_id: run_storage_root_scan(job_id, root_id))
    return LibraryScanResponse(job_ids=job_ids)
