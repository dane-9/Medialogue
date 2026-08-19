from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.db.session import get_db
from app.models.auth import AdminUser
from app.schemas.setup import SetupCompleteRequest, SetupStatusResponse
from app.services.setup import setup_status, write_setup_complete

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status", response_model=SetupStatusResponse)
async def status(
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SetupStatusResponse:
    return await setup_status(db, admin)


@router.put("/complete", response_model=SetupStatusResponse)
async def complete(
    payload: SetupCompleteRequest,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SetupStatusResponse:
    write_setup_complete(payload.complete)
    return await setup_status(db, admin)
