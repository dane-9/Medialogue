from datetime import datetime

from pydantic import BaseModel, Field


class TMDBConfigurationUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    expected_revision: int | None = None


class TMDBTestRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=4096)


class TMDBConfigurationResponse(BaseModel):
    configured: bool
    api_key_configured: bool = False
    enabled: bool = False
    health: str = "unknown"
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    latency_ms: int | None = None
    last_error: str | None = None
    revision: int | None = None


class TMDBTestResponse(BaseModel):
    status: str
    latency_ms: int | None = None
    message: str | None = None
