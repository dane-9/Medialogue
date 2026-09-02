from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_session, require_admin, require_csrf
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.login_rate_limit import login_attempt_key, login_rate_limiter
from app.core.security import digest_token, hash_password, new_token, verify_password
from app.db.bootstrap import initial_setup_token_path, production_bootstrap_required
from app.db.session import get_db
from app.models.auth import AdminUser, AuthSession
from app.schemas.auth import (
    AdminResponse,
    AuthRuntimeConfigResponse,
    AuthSessionResponse,
    BootstrapRequest,
    BootstrapStatusResponse,
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    SecurityResponse,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
_bootstrap_lock = asyncio.Lock()


def _cookie_kwargs() -> dict[str, object]:
    settings = get_settings()
    return {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "max_age": settings.session_ttl_hours * 3600,
        "path": "/",
    }


def _set_session_cookies(response: Response, raw_session: str, raw_csrf: str) -> None:
    settings = get_settings()
    response.set_cookie(settings.session_cookie_name, raw_session, **_cookie_kwargs())
    csrf_kwargs = {key: value for key, value in _cookie_kwargs().items() if key != "httponly"}
    response.set_cookie(settings.csrf_cookie_name, raw_csrf, httponly=False, **csrf_kwargs)


async def _create_auth_session(
    db: AsyncSession,
    response: Response,
    admin: AdminUser,
    *,
    commit: bool = True,
) -> LoginResponse:
    """Create the shared opaque session + CSRF cookie pair for login/bootstrap."""

    settings = get_settings()
    now = datetime.now(timezone.utc)
    await db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    raw_session = new_token()
    raw_csrf = new_token()
    db.add(
        AuthSession(
            admin_id=admin.id,
            token_digest=digest_token(raw_session),
            csrf_digest=digest_token(raw_csrf),
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
        )
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
    _set_session_cookies(response, raw_session, raw_csrf)
    return LoginResponse(
        csrf_token=raw_csrf,
        user=AdminResponse.model_validate(admin),
        default_password_warning=admin.is_default_password,
    )


def _login_throttle_error(retry_after: int) -> AppError:
    return AppError(
        "LOGIN_THROTTLED",
        "Too many failed login attempts. Try again later.",
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    settings = get_settings()
    key = login_attempt_key(payload.username, request.client.host if request.client else None)
    retry_after = login_rate_limiter.retry_after(key)
    if retry_after is not None:
        raise _login_throttle_error(retry_after)

    result = await db.execute(select(AdminUser).where(AdminUser.username == payload.username))
    admin = result.scalar_one_or_none()

    if settings.is_production and (admin is None or admin.is_default_password):
        raise AppError(
            "INITIAL_SETUP_REQUIRED",
            "Complete initial administrator setup before normal login.",
            status_code=401,
        )

    if admin is None or not verify_password(payload.password, admin.password_hash):
        login_rate_limiter.record_failure(key)
        raise AppError("INVALID_CREDENTIALS", "Username or password is incorrect.", status_code=401)

    login_rate_limiter.clear(key)
    return await _create_auth_session(db, response, admin)


@router.get("/bootstrap/status", response_model=BootstrapStatusResponse)
async def bootstrap_status(db: AsyncSession = Depends(get_db)) -> BootstrapStatusResponse:
    settings = get_settings()
    required = settings.is_production and await production_bootstrap_required(db)
    return BootstrapStatusResponse(required=required)


@router.get("/config", response_model=AuthRuntimeConfigResponse)
async def runtime_config() -> AuthRuntimeConfigResponse:
    """Expose non-secret auth transport settings to the frontend at runtime."""

    return AuthRuntimeConfigResponse(csrf_cookie_name=get_settings().csrf_cookie_name)


@router.get("/sessions", response_model=list[AuthSessionResponse])
async def list_sessions(
    request: Request,
    current: AuthSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> list[AuthSessionResponse]:
    settings = get_settings()
    raw_session = request.cookies.get(settings.session_cookie_name)
    current_digest = digest_token(raw_session) if raw_session else None
    now = datetime.now(timezone.utc)
    sessions = (
        await db.scalars(
            select(AuthSession)
            .where(AuthSession.admin_id == current.admin_id, AuthSession.expires_at > now)
            .order_by(AuthSession.created_at.desc())
        )
    ).all()
    return [
        AuthSessionResponse(
            id=session.id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            last_seen_at=session.last_seen_at,
            current=session.id == current.id or session.token_digest == current_digest,
        )
        for session in sessions
    ]


@router.post("/sessions/revoke-others", status_code=204)
async def revoke_other_sessions(
    current: AuthSession = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await db.execute(
        delete(AuthSession).where(
            AuthSession.admin_id == current.admin_id,
            AuthSession.id != current.id,
        )
    )
    await db.commit()
    return Response(status_code=204)


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(
    session_id: UUID,
    current: AuthSession = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if session_id == current.id:
        raise AppError(
            "CURRENT_SESSION_REQUIRED",
            "Use logout to end the current session.",
            status_code=409,
        )
    session = await db.scalar(
        select(AuthSession).where(AuthSession.id == session_id, AuthSession.admin_id == current.admin_id)
    )
    if session is None:
        raise AppError("SESSION_NOT_FOUND", "That session was not found.", status_code=404)
    await db.delete(session)
    await db.commit()
    return Response(status_code=204)


@router.post("/bootstrap", response_model=LoginResponse)
async def bootstrap(
    payload: BootstrapRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    settings = get_settings()
    if not settings.is_production:
        raise AppError(
            "BOOTSTRAP_NOT_REQUIRED",
            "Administrator bootstrap is only available for production first-run setup.",
            status_code=409,
        )

    username = payload.username.strip()
    if not username:
        raise AppError("INVALID_USERNAME", "Administrator username cannot be blank.", status_code=422)
    token_path = initial_setup_token_path(settings)
    try:
        stored_token = token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise AppError(
            "BOOTSTRAP_NOT_AVAILABLE",
            "Initial administrator setup is no longer available.",
            status_code=409,
        ) from exc
    except OSError as exc:
        raise AppError(
            "BOOTSTRAP_UNAVAILABLE",
            "Initial administrator setup is temporarily unavailable.",
            status_code=503,
        ) from exc
    if not stored_token or not secrets.compare_digest(payload.setup_token, stored_token):
        raise AppError("INVALID_SETUP_TOKEN", "The initial setup token is invalid.", status_code=403)

    async with _bootstrap_lock:
        try:
            # SQLite's write-intent transaction serializes competing bootstrap
            # requests before the administrator state is re-checked.
            await db.execute(text("BEGIN IMMEDIATE"))
            admins = (await db.scalars(select(AdminUser).order_by(AdminUser.created_at, AdminUser.id))).all()
            if not admins:
                admin = AdminUser(
                    username=username,
                    password_hash=hash_password(payload.password),
                    is_default_password=False,
                )
                db.add(admin)
                await db.flush()
            elif len(admins) == 1 and admins[0].is_default_password:
                admin = admins[0]
                conflict = await db.scalar(
                    select(AdminUser.id).where(AdminUser.username == username, AdminUser.id != admin.id)
                )
                if conflict is not None:
                    raise AppError(
                        "BOOTSTRAP_USERNAME_CONFLICT",
                        "That administrator username is already in use.",
                        status_code=409,
                    )
                admin.username = username
                admin.password_hash = hash_password(payload.password)
                admin.is_default_password = False
                await db.execute(delete(AuthSession).where(AuthSession.admin_id == admin.id))
            else:
                raise AppError(
                    "BOOTSTRAP_ALREADY_COMPLETED",
                    "Initial administrator setup has already been completed.",
                    status_code=409,
                )
            result = await _create_auth_session(db, response, admin, commit=False)
            await db.commit()
        except AppError:
            await db.rollback()
            raise
        except IntegrityError as exc:
            await db.rollback()
            raise AppError(
                "BOOTSTRAP_CONFLICT",
                "Initial administrator setup could not be completed; try again.",
                status_code=409,
            ) from exc
        except Exception:
            await db.rollback()
            raise

    try:
        token_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # The consumed database state and one-time session are still safe if
        # cleanup loses a filesystem race. A later startup removes stale files.
        import logging

        logging.getLogger(__name__).error("Could not remove the consumed initial setup token file.", exc_info=True)
    return result


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
    await db.execute(
        delete(AuthSession).where(
            AuthSession.admin_id == admin.id,
            AuthSession.id != auth_session.id,
        )
    )
    await db.commit()
    return AdminResponse.model_validate(admin)
