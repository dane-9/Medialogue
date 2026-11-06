from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_APPLICATION_SECRET_FILENAME = "application-secret"
_RUNTIME_SECRET_PLACEHOLDERS = frozenset(
    {
        "change-me-in-production",
        "replace-with-a-long-random-secret",
        "replace-with-a-long-random-secret-at-least-32-characters",
        "example-only-change-me",
        "your-real-random-secret",
    }
)


class Settings(BaseSettings):
    """Runtime configuration.

    All paths are resolved by the application inside the container.  A storage
    media folder is never implicitly created; users explicitly add folders through the
    API and scans are read-only observations.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MEDIALOGUE_", extra="ignore")

    app_name: str = "Medialogue"
    environment: str = "development"
    debug: bool = False
    secret_key: str = "change-me-in-production"
    database_url: str = "sqlite+aiosqlite:////config/medialogue.db"
    config_dir: str = "/config"
    torrent_archive_dir: str = "/torrent-archive"
    google_drive_client_id: str = ""
    google_drive_client_secret: str = ""
    google_drive_redirect_uri: str = ""
    session_cookie_name: str = "medialogue_session"
    csrf_cookie_name: str = "medialogue_csrf"
    session_ttl_hours: int = 24 * 14
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    default_admin_username: str = "admin"
    default_admin_password: str = "adminadmin"
    bootstrap_admin: bool = True
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    @field_validator("secret_key", mode="before")
    @classmethod
    def normalize_secret_key(cls, value: object) -> str:
        # Compose intentionally passes an empty value when the operator does
        # not provide an override. Treat that exactly like an unset value so
        # production startup can generate /config/application-secret.
        if value is None or (isinstance(value, str) and not value.strip()):
            return "change-me-in-production"
        if not isinstance(value, str):
            raise ValueError("MEDIALOGUE_SECRET_KEY must be a string")
        normalized = value.strip()
        if not is_placeholder_secret(normalized) and len(normalized) < 16:
            raise ValueError("MEDIALOGUE_SECRET_KEY must be at least 16 characters")
        return normalized

    @field_validator("database_url")
    @classmethod
    def sqlite_only(cls, value: str) -> str:
        if not value.startswith(("sqlite+aiosqlite://", "sqlite://")):
            raise ValueError("Medialogue supports SQLite databases only")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


def application_secret_path(settings: Settings | None = None) -> Path:
    current = settings or get_settings()
    return Path(current.config_dir) / _APPLICATION_SECRET_FILENAME


def is_placeholder_secret(value: str | None) -> bool:
    normalized = value.strip().casefold() if value else ""
    return (
        not normalized
        or normalized in _RUNTIME_SECRET_PLACEHOLDERS
        or normalized.startswith("change-me")
        or normalized.startswith("replace-with")
    )


def _validate_persisted_secret(raw: str, path: Path) -> str:
    if "\n" in raw or "\r" in raw:
        raise RuntimeError(f"Persisted application secret is malformed: {path}.")
    value = raw.strip()
    if not value or len(value) < 32 or is_placeholder_secret(value):
        raise RuntimeError(f"Persisted application secret is malformed: {path}.")
    return value


def _create_application_secret(path: Path) -> str:
    value = secrets.token_urlsafe(48)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _validate_persisted_secret(path.read_text(encoding="utf-8"), path)

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return value


def _legacy_environment_secret() -> str | None:
    """Read the pre-hardening SECRET_KEY name for one compatibility cycle."""

    raw = os.environ.get("SECRET_KEY")
    if raw is None:
        return None
    value = raw.strip()
    if not value or is_placeholder_secret(value):
        return None
    if len(value) < 16:
        raise RuntimeError("Legacy SECRET_KEY must be at least 16 characters.")
    return value


def _integration_pair_exists(settings: Settings) -> bool:
    config_dir = Path(settings.config_dir)
    return (config_dir / "medialogue.json").exists() and (config_dir / "secrets.enc").exists()


def _find_legacy_integration_secret(settings: Settings) -> str | None:
    """Return a known legacy key only if it decrypts the existing secret store."""

    if not _integration_pair_exists(settings):
        return None
    from app.core.integration_config import validate_existing_integration_secret

    # Keep the order deterministic. The first value is the historical runtime
    # default used by released Compose examples before automatic key creation.
    candidates = (
        "replace-with-a-long-random-secret",
        "change-me-in-production",
        "replace-with-a-long-random-secret-at-least-32-characters",
        "example-only-change-me",
        "your-real-random-secret",
    )
    for candidate in candidates:
        if validate_existing_integration_secret(settings.config_dir, candidate):
            return candidate
    return None


def _repair_or_migrate_existing_integration_secret(
    settings: Settings,
    *,
    target_secret: str,
    legacy_secret: str | None = None,
) -> None:
    """Ensure an existing encrypted integration store uses ``target_secret``.

    A persisted generated key can survive a crash between key creation and the
    secrets-file rewrite. In that case the target key will not decrypt yet, so
    we retry the known legacy key and complete the re-encryption.
    """

    if not _integration_pair_exists(settings):
        return
    from app.core.integration_config import (
        migrate_existing_integration_secret,
        validate_existing_integration_secret,
    )

    if validate_existing_integration_secret(settings.config_dir, target_secret):
        return
    old_secret = legacy_secret or _find_legacy_integration_secret(settings)
    if old_secret is None:
        raise RuntimeError(
            "Existing integration secrets cannot be decrypted with the configured or known legacy application keys. "
            "Restore the previous MEDIALOGUE_SECRET_KEY/SECRET_KEY before starting Medialogue."
        )
    migrate_existing_integration_secret(settings.config_dir, old_secret, target_secret)


def ensure_runtime_secret(settings: Settings | None = None) -> str:
    """Resolve a stable production secret before encrypted configuration access.

    Explicit MEDIALOGUE_SECRET_KEY remains authoritative. The historical
    SECRET_KEY environment name is accepted for upgrade compatibility. If no
    operator key exists, production persists a generated key under /config.
    Existing stores encrypted with a documented legacy placeholder are
    decrypted successfully *before* a new key is persisted, then re-encrypted.
    """

    current = settings or get_settings()
    if not current.is_production:
        return current.secret_key

    if not is_placeholder_secret(current.secret_key):
        if "\n" in current.secret_key or "\r" in current.secret_key:
            raise RuntimeError("MEDIALOGUE_SECRET_KEY must not contain a newline.")
        return current.secret_key

    legacy_env = _legacy_environment_secret()
    if legacy_env is not None:
        current.secret_key = legacy_env
        return legacy_env

    path = application_secret_path(current)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        legacy_store_secret = _find_legacy_integration_secret(current)
        if _integration_pair_exists(current) and legacy_store_secret is None:
            # Do not create a new key until we have proved how to decrypt the
            # existing credentials. Persisting an unrelated key here would
            # turn an upgrade/configuration problem into data loss.
            raise RuntimeError(
                "Existing integration secrets require their previous application key. "
                "Set MEDIALOGUE_SECRET_KEY (or legacy SECRET_KEY) to the key used by this installation and restart."
            )
        resolved = _create_application_secret(path)
        if legacy_store_secret is not None:
            _repair_or_migrate_existing_integration_secret(
                current,
                target_secret=resolved,
                legacy_secret=legacy_store_secret,
            )
    except OSError as exc:
        raise RuntimeError(f"Could not read persisted application secret: {path}.") from exc
    else:
        resolved = _validate_persisted_secret(raw, path)
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            raise RuntimeError(f"Could not secure persisted application secret: {path}.") from exc
        _repair_or_migrate_existing_integration_secret(current, target_secret=resolved)

    current.secret_key = resolved
    return resolved


_override: Settings | None = None


def set_settings(settings: Settings) -> None:
    global _override
    _override = settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _override or Settings()
