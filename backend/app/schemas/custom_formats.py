from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from .common import ORMModel


CUSTOM_FORMAT_SCHEMA_VERSION = 1


class CustomFormatScope(str, Enum):
    MOVIES = "movies"
    SHOWS = "shows"
    BOTH = "both"


class CustomFormatConditionType(str, Enum):
    RELEASE_TITLE = "release_title"
    RELEASE_GROUP = "release_group"
    QUALITY = "quality"
    QUALITY_MODIFIER = "quality_modifier"
    RESOLUTION = "resolution"
    SOURCE = "source"
    EDITION = "edition"
    LANGUAGE = "language"
    INDEXER = "indexer"
    WEB_PROVIDER = "web_provider"
    VIDEO_CODEC = "video_codec"
    AUDIO_CODEC = "audio_codec"
    AUDIO_CHANNELS = "audio_channels"
    HDR_TYPE = "hdr_type"
    RELEASE_ATTRIBUTE = "release_attribute"


REGEX_CONDITION_TYPES = {
    CustomFormatConditionType.RELEASE_TITLE,
    CustomFormatConditionType.RELEASE_GROUP,
}


class CustomFormatConditionInput(BaseModel):
    id: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=256)
    type: CustomFormatConditionType
    value: str | list[str] | None = None
    pattern: str | None = Field(default=None, max_length=4096)
    required: bool = False
    negate: bool = False
    case_sensitive: bool = False
    group: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_condition_shape(self) -> "CustomFormatConditionInput":
        if self.type in REGEX_CONDITION_TYPES:
            pattern = self.pattern if self.pattern is not None else (self.value if isinstance(self.value, str) else None)
            if not pattern or not pattern.strip():
                raise ValueError("release title/group conditions require a regex pattern")
            self.pattern = pattern
            self.value = None
        else:
            values = self.value if isinstance(self.value, list) else ([self.value] if self.value is not None else [])
            if not any(str(item).strip() for item in values):
                raise ValueError(f"{self.type.value} conditions require a value")
            self.pattern = None
            # Case sensitivity is an advanced regex setting only.
            self.case_sensitive = False
        return self


class CustomFormatCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    media_scope: CustomFormatScope = CustomFormatScope.BOTH
    enabled: bool = True
    conditions: list[CustomFormatConditionInput] = Field(min_length=1, max_length=100)


class CustomFormatUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    media_scope: CustomFormatScope | None = None
    enabled: bool | None = None
    conditions: list[CustomFormatConditionInput] | None = Field(default=None, min_length=1, max_length=100)
    expected_revision: int | None = Field(default=None, ge=1)


class CustomFormatConditionResponse(ORMModel):
    id: str
    name: str | None
    type: CustomFormatConditionType
    value: Any = None
    pattern: str | None = None
    required: bool
    negate: bool
    case_sensitive: bool
    group: str | None = None


class CustomFormatResponse(ORMModel):
    id: UUID
    name: str
    description: str | None
    media_scope: CustomFormatScope
    enabled: bool
    schema_version: int
    conditions: list[CustomFormatConditionResponse]
    condition_count: int
    used_by_profiles: int
    revision: int
    created_at: datetime
    updated_at: datetime


class CustomFormatTestDefinition(BaseModel):
    id: UUID | None = None
    name: str = Field(default="Test Format", min_length=1, max_length=256)
    description: str | None = None
    media_scope: CustomFormatScope = CustomFormatScope.BOTH
    enabled: bool = True
    conditions: list[CustomFormatConditionInput] = Field(min_length=1, max_length=100)


class CustomFormatTestRequest(BaseModel):
    release_name: str = Field(min_length=1, max_length=8192)
    custom_format: CustomFormatTestDefinition
    indexer: str | None = Field(default=None, max_length=256)
    languages: list[str] = Field(default_factory=list, max_length=64)


class CustomFormatTestAllRequest(BaseModel):
    release_name: str = Field(min_length=1, max_length=8192)
    media_scope: CustomFormatScope = CustomFormatScope.BOTH
    indexer: str | None = Field(default=None, max_length=256)
    languages: list[str] = Field(default_factory=list, max_length=64)
    include_disabled: bool = False
    quality_profile_id: UUID | None = None


class CustomFormatEvaluationResponse(BaseModel):
    custom_format_id: str
    custom_format_name: str
    matched: bool
    conditions: list[dict[str, Any]]
    group_results: dict[str, bool]
    profile_score: int | None = None
    contribution: int | None = None
    error: str | None = None


class CustomFormatTestResponse(BaseModel):
    parsed: dict[str, Any]
    evaluation: CustomFormatEvaluationResponse


class CustomFormatTestAllResponse(BaseModel):
    parsed: dict[str, Any]
    formats: list[CustomFormatEvaluationResponse]
    matched_count: int
    quality_profile_id: UUID | None = None
    quality_profile_name: str | None = None
    total_score: int | None = None


class CustomFormatExportItem(BaseModel):
    schema_version: int = CUSTOM_FORMAT_SCHEMA_VERSION
    name: str
    description: str | None = None
    media_scope: CustomFormatScope = CustomFormatScope.BOTH
    enabled: bool = True
    conditions: list[CustomFormatConditionInput]


class CustomFormatExportBundle(BaseModel):
    application: str = "Medialogue"
    schema_version: int = CUSTOM_FORMAT_SCHEMA_VERSION
    custom_formats: list[CustomFormatExportItem]


class CustomFormatImportBundle(BaseModel):
    # Import intentionally requires the Medialogue marker/version instead of
    # defaulting them. This prevents unrelated/Radarr JSON from being
    # accidentally interpreted as our application-owned schema.
    application: str
    schema_version: int
    custom_formats: list[CustomFormatExportItem] = Field(min_length=1, max_length=500)


class CustomFormatImportResponse(BaseModel):
    imported: list[CustomFormatResponse]
    count: int
