from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    All paths are resolved by the application inside the container.  A storage
    root is never implicitly created; users explicitly add roots through the
    API and scans are read-only observations.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MEDIALOGUE_", extra="ignore")

    app_name: str = "Medialogue"
    environment: str = "development"
    debug: bool = False
    secret_key: str = Field(default="change-me-in-production", min_length=16)
    database_url: str = "postgresql+asyncpg://medialogue:medialogue@postgres:5432/medialogue"
    config_dir: str = "/config"
    torrent_archive_dir: str = "/torrent-archive"
    recovery_export_dir: str = "/config/recovery-exports"
    recovery_export_retention_hours: int = 24
    pg_basebackup_bin: str = "/usr/lib/postgresql/16/bin/pg_basebackup"
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

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


_override: Settings | None = None


def set_settings(settings: Settings) -> None:
    global _override
    _override = settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _override or Settings()
