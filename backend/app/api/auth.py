from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_session, require_admin, require_csrf
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import digest_token, hash_password, new_token, verify_password
from app.db.session import get_db
from app.models.auth import AdminUser, AuthSession
from app.schemas.auth import AdminResponse, LoginRequest, LoginResponse, PasswordChangeRequest, SecurityResponse

router = APIRouter(prefix="/auth", tags=["authentication"])


def _cookie_kwargs() -> dict[str, object]:
    settings = get_settings()
    return {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "max_age": settings.session_ttl_hours * 3600,
        "path": "/",
    }


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    settings = get_settings()
    result = await db.execute(select(AdminUser).where(AdminUser.username == payload.username))
    admin = result.scalar_one_or_none()
    if admin is None or not verify_password(payload.password, admin.password_hash):
        raise AppError("INVALID_CREDENTIALS", "Username or password is incorrect.", status_code=401)

    # Opportunistically prune expired sessions so a long-running personal
    # install cannot accumulate an unbounded session table.
    now = datetime.now(timezone.utc)
    await db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))

    raw_session = new_token()
    raw_csrf = new_token()
    auth_session = AuthSession(
        admin_id=admin.id,
        token_digest=digest_token(raw_session),
        csrf_digest=digest_token(raw_csrf),
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(auth_session)
    await db.commit()
    response.set_cookie(settings.session_cookie_name, raw_session, **_cookie_kwargs())
    # CSRF token is intentionally readable by the frontend and is echoed in the
    # response to support clients which do not expose cookie values.
    response.set_cookie(settings.csrf_cookie_name, raw_csrf, httponly=False, **{k: v for k, v in _cookie_kwargs().items() if k != "httponly"})
    return LoginResponse(
        csrf_token=raw_csrf,
        user=AdminResponse.model_validate(admin),
        default_password_warning=admin.is_default_password,
    )


@router.post("/logout", status_code=204)
async def logout(response: Response, auth_session: AuthSession = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> Response:
    settings = get_settings()
    await db.execute(delete(AuthSession).where(AuthSession.id == auth_session.id))
    await db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return response


@router.get("/me", response_model=AdminResponse)
async def me(admin: AdminUser = Depends(require_admin)) -> AdminResponse:
    return AdminResponse.model_validate(admin)


@router.get("/security", response_model=SecurityResponse)
async def security(auth_session: AuthSession = Depends(get_current_session)) -> SecurityResponse:
    return SecurityResponse(default_password_warning=auth_session.admin.is_default_password, session_expires_at=auth_session.expires_at)


@router.post("/password", response_model=AdminResponse)
async def change_password(
    payload: PasswordChangeRequest,
    auth_session: AuthSession = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> AdminResponse:
    admin = auth_session.admin
    if not verify_password(payload.current_password, admin.password_hash):
        raise AppError("INVALID_PASSWORD", "Current password is incorrect.", status_code=400)
    admin.password_hash = hash_password(payload.new_password)
    admin.is_default_password = False
    # A password change invalidates every other browser/session for the single
    # administrator account. Keep the session that performed the change so the
    # current UI does not unexpectedly log itself out.
    await db.execute(
        delete(AuthSession).where(
            AuthSession.admin_id == admin.id,
            AuthSession.id != auth_session.id,
        )
    )
    await db.commit()
    return AdminResponse.model_validate(admin)
