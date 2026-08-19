from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import digest_token
from app.db.session import get_db
from app.models.auth import AdminUser, AuthSession


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def get_current_session(request: Request, db: AsyncSession = Depends(get_db)) -> AuthSession:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        raise AppError("AUTHENTICATION_REQUIRED", "Authentication is required.", status_code=401)
    result = await db.execute(
        select(AuthSession).options(selectinload(AuthSession.admin)).where(AuthSession.token_digest == digest_token(raw))
    )
    auth_session = result.scalar_one_or_none()
    if auth_session is None or _as_utc(auth_session.expires_at) <= datetime.now(timezone.utc):
        raise AppError("AUTHENTICATION_REQUIRED", "Session is missing or expired.", status_code=401)
    auth_session.last_seen_at = datetime.now(timezone.utc)
    return auth_session


async def require_admin(auth_session: AuthSession = Depends(get_current_session)) -> AdminUser:
    return auth_session.admin


async def require_csrf(request: Request, auth_session: AuthSession = Depends(get_current_session)) -> AuthSession:
    """Require a double-submit CSRF token for all state-changing requests."""

    settings = get_settings()
    header_token = request.headers.get("X-CSRF-Token")
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    if not header_token or not cookie_token or header_token != cookie_token or digest_token(header_token) != auth_session.csrf_digest:
        raise AppError("CSRF_INVALID", "A valid CSRF token is required.", status_code=403)
    return auth_session
