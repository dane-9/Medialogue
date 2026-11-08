from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, application_secret_path, ensure_runtime_secret, is_placeholder_secret
from app.core.errors import AppError
from app.core.integration_config import IntegrationConfigStore
from app.core.security import consume_confirmation_jti
from app.schemas.custom_formats import CustomFormatConditionInput




def _production_settings(config_dir: Path, *, secret_key: str = "") -> Settings:
    return Settings(
        environment="production",
        config_dir=str(config_dir),
        secret_key=secret_key,
        database_url=f"sqlite+aiosqlite:///{config_dir / 'medialogue.db'}",
    )


def test_compose_style_blank_secret_is_treated_as_unset_and_generates_persistent_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIALOGUE_ENVIRONMENT", "production")
    monkeypatch.setenv("MEDIALOGUE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("MEDIALOGUE_SECRET_KEY", "")

    settings = Settings(_env_file=None)
    assert is_placeholder_secret(settings.secret_key)

    first = ensure_runtime_secret(settings)
    secret_path = application_secret_path(settings)
    assert len(first) >= 32
    assert not is_placeholder_secret(first)
    assert secret_path.read_text(encoding="utf-8") == first
    if os.name != "nt":
        assert secret_path.stat().st_mode & 0o777 == 0o600

    restarted = _production_settings(tmp_path)
    second = ensure_runtime_secret(restarted)
    assert second == first


def test_short_explicit_non_placeholder_secret_is_still_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 16 characters"):
        Settings(secret_key="too-short")


def test_existing_unknown_encrypted_store_does_not_persist_an_unrelated_generated_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_key = "operator-custom-old-key-123456789"
    store = IntegrationConfigStore(tmp_path, old_key)
    store.ensure_initialized()
    store.save_tmdb(api_key="must-survive", enabled=True)

    settings = _production_settings(tmp_path)
    with pytest.raises(RuntimeError, match="application key they were saved with"):
        ensure_runtime_secret(settings)

    assert not application_secret_path(settings).exists()
    assert IntegrationConfigStore(tmp_path, old_key).get_tmdb().api_key == "must-survive"


class _NonceSession:
    def __init__(self, *, replay: bool = False) -> None:
        self.replay = replay
        self.executed = 0
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement):
        self.executed += 1

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1
        if self.replay:
            raise IntegrityError("insert", {}, RuntimeError("duplicate"))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _confirmation_payload() -> dict[str, object]:
    return {
        "jti": "single-use-token-id",
        "purpose": "test_destructive_action",
        "exp": int(time.time()) + 300,
    }


def test_confirmation_nonce_is_committed_immediately_before_external_side_effects() -> None:
    session = _NonceSession()
    asyncio.run(consume_confirmation_jti(session, _confirmation_payload()))  # type: ignore[arg-type]

    assert session.executed == 1  # expired nonce cleanup
    assert len(session.added) == 1
    assert session.flushes == 1
    assert session.commits == 1
    assert session.rollbacks == 0


def test_confirmation_nonce_replay_rolls_back_and_returns_controlled_error() -> None:
    session = _NonceSession(replay=True)
    with pytest.raises(AppError) as raised:
        asyncio.run(consume_confirmation_jti(session, _confirmation_payload()))  # type: ignore[arg-type]

    assert raised.value.code == "CONFIRMATION_REPLAY"
    assert session.commits == 0
    assert session.rollbacks == 1


def test_confirmation_nonce_survives_caller_rollback_in_real_database(tmp_path: Path) -> None:
    pytest.importorskip("aiosqlite")

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.domain import ConfirmationNonce

    async def _exercise() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'nonce.db'}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(ConfirmationNonce.__table__.create)

            payload = _confirmation_payload()
            async with sessions() as db:
                await consume_confirmation_jti(db, payload)
                # Simulate a later destructive operation failing and the caller
                # rolling back its own transaction. The consumed JTI must stay.
                await db.rollback()

            async with sessions() as db:
                with pytest.raises(AppError) as replayed:
                    await consume_confirmation_jti(db, payload)
                assert replayed.value.code == "CONFIRMATION_REPLAY"
        finally:
            await engine.dispose()

    asyncio.run(_exercise())


def test_expired_confirmation_is_rejected_before_database_mutation() -> None:
    session = _NonceSession()
    payload = _confirmation_payload()
    payload["exp"] = int(time.time()) - 1

    with pytest.raises(AppError) as raised:
        asyncio.run(consume_confirmation_jti(session, payload))  # type: ignore[arg-type]

    assert raised.value.code == "CONFIRMATION_EXPIRED"
    assert session.executed == 0
    assert session.added == []
    assert session.commits == 0


def test_custom_format_schema_matches_runtime_regex_limit() -> None:
    CustomFormatConditionInput(pattern="a" * 1024)
    with pytest.raises(ValidationError):
        CustomFormatConditionInput(pattern="a" * 1025)


def test_dev_container_does_not_force_public_vite_bind() -> None:
    repo = Path(__file__).resolve().parents[2]
    dev_dockerfile = (repo / "docker" / "dev.Dockerfile").read_text(encoding="utf-8")
    assert '--host", "0.0.0.0' not in dev_dockerfile
    assert 'CMD ["npm", "run", "dev"]' in dev_dockerfile


def test_frontend_lockfile_uses_patched_vitest_release() -> None:
    repo = Path(__file__).resolve().parents[2]
    package = json.loads((repo / "frontend" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((repo / "frontend" / "package-lock.json").read_text(encoding="utf-8"))

    def version(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in value.lstrip("^~").split(".")[:3])

    # At least the patched releases; newer security fixes must not fail this.
    assert version(package["devDependencies"]["vitest"]) >= (4, 1, 12)
    assert version(lock["packages"]["node_modules/vitest"]["version"]) >= (4, 1, 12)
    assert version(lock["packages"]["node_modules/@vitest/mocker"]["version"]) >= (4, 1, 12)


def test_production_unknown_username_counts_toward_login_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("aiosqlite")

    from fastapi import Request, Response

    import app.api.auth as auth_api
    from app.schemas.auth import LoginRequest

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _LoginDb:
        async def execute(self, _statement):
            return _Result()

    async def _setup_complete(_db) -> bool:
        return False

    settings = Settings(environment="production", secret_key="production-test-secret-123456789")
    monkeypatch.setattr(auth_api, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_api, "production_bootstrap_required", _setup_complete)
    auth_api.login_rate_limiter.reset()

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    db = _LoginDb()

    try:
        for _ in range(5):
            with pytest.raises(AppError) as raised:
                asyncio.run(
                    auth_api.login(
                        LoginRequest(username="nobody", password="wrong-password"),
                        request,
                        Response(),
                        db,  # type: ignore[arg-type]
                    )
                )
            assert raised.value.code == "INVALID_CREDENTIALS"

        with pytest.raises(AppError) as throttled:
            asyncio.run(
                auth_api.login(
                    LoginRequest(username="nobody", password="wrong-password"),
                    request,
                    Response(),
                    db,  # type: ignore[arg-type]
                )
            )
        assert throttled.value.code == "LOGIN_THROTTLED"
        assert throttled.value.status_code == 429
        assert "Retry-After" in throttled.value.headers
    finally:
        auth_api.login_rate_limiter.reset()


def test_parallel_wrong_logins_cannot_outrun_the_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("aiosqlite")

    from fastapi import Request, Response

    import app.api.auth as auth_api
    from app.schemas.auth import LoginRequest

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _SlowDb:
        async def execute(self, _statement):
            # Yield, so every request reaches this point before any finishes.
            await asyncio.sleep(0.01)
            return _Result()

    async def _setup_complete(_db) -> bool:
        return False

    settings = Settings(environment="production", secret_key="production-test-secret-123456789")
    monkeypatch.setattr(auth_api, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_api, "production_bootstrap_required", _setup_complete)
    auth_api.login_rate_limiter.reset()
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/auth/login", "headers": [],
                       "client": ("127.0.0.1", 12345)})

    async def attempt() -> str:
        try:
            await auth_api.login(LoginRequest(username="nobody", password="wrong-password"), request, Response(),
                                 _SlowDb())  # type: ignore[arg-type]
        except AppError as exc:
            return exc.code
        return "OK"

    async def burst() -> list[str]:
        return await asyncio.gather(*(attempt() for _ in range(12)))

    try:
        codes = asyncio.run(burst())
    finally:
        auth_api.login_rate_limiter.reset()
    assert codes.count("INVALID_CREDENTIALS") == auth_api.login_rate_limiter.failure_limit
    assert codes.count("LOGIN_THROTTLED") == 12 - auth_api.login_rate_limiter.failure_limit
