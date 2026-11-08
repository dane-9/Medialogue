"""Recent log lines, for reading errors without access to the container's output."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import require_admin
from app.core.logging import recent_logs
from app.models.auth import AdminUser

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
async def read_logs(
    limit: int = Query(500, ge=1, le=2000),
    level: str = Query("INFO", description="Lowest level to include: DEBUG, INFO, WARNING, ERROR."),
    contains: str | None = Query(None, max_length=200, description="Only lines whose message or logger contains this."),
    _: AdminUser = Depends(require_admin),
) -> dict[str, Any]:
    """The latest lines kept in memory since the last start, oldest first."""

    floor = logging.getLevelName(level.upper())
    floor = floor if isinstance(floor, int) else logging.INFO
    needle = (contains or "").casefold()
    lines = [
        line for line in recent_logs.lines
        if logging.getLevelName(line.get("level", "INFO")) >= floor
        and (not needle or needle in str(line.get("message", "")).casefold() or needle in str(line.get("logger", "")).casefold())
    ]
    return {"items": lines[-limit:], "kept": len(recent_logs.lines)}
