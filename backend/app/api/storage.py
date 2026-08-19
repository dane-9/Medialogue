from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import AccessMode, Job, MediaDirectory, MediaType, RemotePathMapping, StorageRoot
from app.schemas.common import Collection, DeleteResponse
from app.schemas.jobs import JobAcceptedResponse, JobResponse
from app.schemas.storage import (
    RemotePathMappingCreate,
    RemotePathMappingResponse,
    StorageRootCreate,
    StorageRootResponse,
    StorageRootUpdate,
)
from app.services.jobs import create_job
from app.api.operations import active_operations_enabled
from app.services.library_scan import run_storage_root_scan

router = APIRouter(tags=["storage"])


def _resolved_path(path: str) -> str:
    # resolve(strict=False) canonicalizes `..` but does not create a directory
    # or require a currently mounted NAS path.
    return str(Path(path).expanduser().resolve(strict=False))


async def _root_response(db: AsyncSession, root: StorageRoot) -> StorageRootResponse:
    affected = 0
    if root.last_health == "unavailable":
        affected = int(
            await db.scalar(
                select(func.count()).select_from(MediaDirectory).where(MediaDirectory.storage_root_id == root.id)
            )
            or 0
        )
    return StorageRootResponse.model_validate(root).model_copy(
        update={"affected_media_count": affected}
    )


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


@router.delete("/storage-roots/{root_id}", response_model=DeleteResponse)
async def delete_storage_root(
    root_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    root = await db.get(StorageRoot, root_id)
    if root is None:
        raise AppError("NOT_FOUND", "Storage root was not found.", status_code=404)
    # Removing a configuration record never removes media files.
    await db.delete(root)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError("STORAGE_ROOT_IN_USE", "Storage root still has attached media directories.", status_code=409) from exc
    return DeleteResponse(id=root_id)


@router.post("/storage-roots/{root_id}/scan", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def scan_storage_root(
    root_id: UUID,
    background_tasks: BackgroundTasks,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedResponse:
    root = await db.get(StorageRoot, root_id)
    if root is None:
        raise AppError("NOT_FOUND", "Storage root was not found.", status_code=404)
    if not root.enabled:
        raise AppError("STORAGE_ROOT_DISABLED", "Storage root is disabled.", status_code=409)
    if not active_operations_enabled():
        raise AppError(
            "ACTIVE_OPERATIONS_LOCKED",
            "Enable Active Operations before starting a storage scan.",
            status_code=423,
        )
    job = await create_job(db, "storage_root_scan", summary={"storage_root_id": str(root.id), "path": root.resolved_root_path})
    await db.commit()
    background_tasks.add_task(run_storage_root_scan, job.id, root.id)
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
    mapping = RemotePathMapping(**payload.model_dump())
    db.add(mapping)
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
