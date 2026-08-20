from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, Field


class PlexConfigurationUpdate(BaseModel):
    url: AnyHttpUrl
    token: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    expected_revision: int | None = None


class PlexTestRequest(BaseModel):
    url: AnyHttpUrl | None = None
    token: str | None = Field(default=None, max_length=4096)


class PlexConfigurationResponse(BaseModel):
    configured: bool
    url: str | None = None
    token_configured: bool = False
    enabled: bool = False
    health: str = "unknown"
    machine_identifier: str | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    latency_ms: int | None = None
    last_error: str | None = None
    revision: int | None = None


class PlexHealthResponse(BaseModel):
    configured: bool = False
    enabled: bool = False
    status: str = "unknown"
    machine_identifier: str | None = None
    last_success: datetime | None = None
    latency_ms: int | None = None
    last_error: str | None = None


class PlexTestResponse(BaseModel):
    status: str
    machine_identifier: str | None = None
    latency_ms: int | None = None
    message: str | None = None


class PlexRecheckResponse(BaseModel):
    movie_id: str | None = None
    show_id: str | None = None
    state: str
    checked_releases: int
    matched_releases: int
    not_found_releases: int
    multiple_version_releases: int
    conflict_releases: int
