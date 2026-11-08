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


def _integration_pair_exists(settings: Settings) -> bool:
    config_dir = Path(settings.config_dir)
    return (config_dir / "medialogue.json").exists() and (config_dir / "secrets.enc").exists()


def ensure_runtime_secret(settings: Settings | None = None) -> str:
    """Resolve a stable production secret before encrypted configuration access.

    MEDIALOGUE_SECRET_KEY wins when set. Otherwise production generates a key
    once and keeps it in /config/application-secret. A key is never generated
    while encrypted secrets already exist: they would become unreadable.
    """

    current = settings or get_settings()
    if not current.is_production:
        return current.secret_key

    if not is_placeholder_secret(current.secret_key):
        if "\n" in current.secret_key or "\r" in current.secret_key:
            raise RuntimeError("MEDIALOGUE_SECRET_KEY must not contain a newline.")
        return current.secret_key

    path = application_secret_path(current)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if _integration_pair_exists(current):
            raise RuntimeError(
                "Existing integration secrets need the application key they were saved with. "
                "Restore /config/application-secret or set MEDIALOGUE_SECRET_KEY, then restart."
            )
        resolved = _create_application_secret(path)
    except OSError as exc:
        raise RuntimeError(f"Could not read persisted application secret: {path}.") from exc
    else:
        resolved = _validate_persisted_secret(raw, path)
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            raise RuntimeError(f"Could not secure persisted application secret: {path}.") from exc

    current.secret_key = resolved
    return resolved


_override: Settings | None = None


def set_settings(settings: Settings) -> None:
    global _override
    _override = settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _override or Settings()
