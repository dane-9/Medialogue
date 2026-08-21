from pydantic import BaseModel
from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin, require_csrf
from app.models.auth import AdminUser

router = APIRouter(tags=["operations"])


class OperationsState(BaseModel):
    enabled: bool


def active_operations_enabled() -> bool:
    """Compatibility shim: operations are always available.

    Older clients and internal call sites still query this helper.  Keeping it
    always true removes the global kill-switch without forcing a flag-day API
    change across existing installations.
    """

    return True


def reset_active_operations() -> None:
    """Retained for compatibility with application/test startup."""

    return None


@router.get("/operations", response_model=OperationsState, include_in_schema=False)
async def get_operations(_: AdminUser = Depends(require_admin)) -> OperationsState:
    return OperationsState(enabled=True)


@router.put("/operations", response_model=OperationsState, include_in_schema=False)
async def set_operations(
    payload: OperationsState, _: object = Depends(require_csrf)
) -> OperationsState:
    # Deprecated compatibility endpoint.  The former global toggle no longer
    # changes runtime behaviour, so old frontends can call it safely while new
    # frontends simply omit the control.
    del payload
    return OperationsState(enabled=True)
