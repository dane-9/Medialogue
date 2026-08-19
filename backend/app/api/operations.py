from pydantic import BaseModel
from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin, require_csrf
from app.models.auth import AdminUser

router = APIRouter(tags=["operations"])

# Safe by construction: every process/restart begins locked. A future durable
# settings service may persist an explicit choice, but must retain OFF as the
# fresh-install default.
_active_operations = False


class OperationsState(BaseModel):
    enabled: bool


def active_operations_enabled() -> bool:
    return _active_operations


def reset_active_operations() -> None:
    global _active_operations
    _active_operations = False


@router.get("/operations", response_model=OperationsState)
async def get_operations(_: AdminUser = Depends(require_admin)) -> OperationsState:
    return OperationsState(enabled=_active_operations)


@router.put("/operations", response_model=OperationsState)
async def set_operations(
    payload: OperationsState, _: object = Depends(require_csrf)
) -> OperationsState:
    global _active_operations
    _active_operations = payload.enabled
    return OperationsState(enabled=_active_operations)
