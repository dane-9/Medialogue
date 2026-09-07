from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import ORMModel


CUSTOM_FORMAT_SCHEMA_VERSION = 1


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


REGEX_CONDITION_TYPES = set(CustomFormatConditionType)


class StrictCustomFormatModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomFormatConditionInput(StrictCustomFormatModel):
    id: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=256)
    # Which parsed field the pattern runs against. Built-in formats match typed
    # fields such as the audio codec, so a client echoing a condition back must
    # be able to preserve that; omitting it still means the raw release title.
    type: CustomFormatConditionType = CustomFormatConditionType.RELEASE_TITLE
    pattern: str | None = Field(default=None, max_length=1024)
    required: bool = False
    negate: bool = False
    case_sensitive: bool = False
    group: str | None = Field(default=None, max_length=128)
    score_offset: int = Field(default=0, ge=-100000, le=100000)

    @model_validator(mode="after")
    def validate_condition_shape(self) -> "CustomFormatConditionInput":
        if not self.pattern or not self.pattern.strip():
            raise ValueError("Custom Format conditions require a regex pattern")
        return self


class CustomFormatCreate(StrictCustomFormatModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    conditions: list[CustomFormatConditionInput] = Field(min_length=1, max_length=100)
    supersedes: list[UUID] = Field(default_factory=list, max_length=500)


class CustomFormatUpdate(StrictCustomFormatModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None
    conditions: list[CustomFormatConditionInput] | None = Field(default=None, min_length=1, max_length=100)
    supersedes: list[UUID] | None = Field(default=None, max_length=500)
    expected_revision: int | None = Field(default=None, ge=1)


class CustomFormatConditionResponse(ORMModel):
    id: str
    name: str | None
    type: CustomFormatConditionType = CustomFormatConditionType.RELEASE_TITLE
    pattern: str | None = None
    required: bool
    negate: bool
    case_sensitive: bool
    group: str | None = None
    score_offset: int = 0

    @field_validator("type", mode="before")
    @classmethod
    def default_missing_type(cls, value: Any) -> Any:
        # Conditions stored before the type was carried over the API read back
        # as an empty string; those have always meant the raw release title.
        return value or CustomFormatConditionType.RELEASE_TITLE


class CustomFormatResponse(ORMModel):
    id: UUID
    name: str
    description: str | None
    enabled: bool
    builtin: bool = False
    schema_version: int
    conditions: list[CustomFormatConditionResponse]
    condition_count: int
    supersedes: list[UUID] = Field(default_factory=list)
    used_by_profiles: int
    revision: int
    created_at: datetime
    updated_at: datetime


class CustomFormatSection(StrictCustomFormatModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=128)
    format_ids: list[UUID] = Field(default_factory=list, max_length=500)


class CustomFormatLayout(StrictCustomFormatModel):
    sections: list[CustomFormatSection] = Field(min_length=1, max_length=100)


class CustomFormatTestDefinition(StrictCustomFormatModel):
    id: UUID | None = None
    name: str = Field(default="Test Format", min_length=1, max_length=256)
    description: str | None = None
    enabled: bool = True
    conditions: list[CustomFormatConditionInput] = Field(min_length=1, max_length=100)
    supersedes: list[UUID] = Field(default_factory=list, max_length=500)


class CustomFormatTestRequest(StrictCustomFormatModel):
    release_name: str = Field(min_length=1, max_length=8192)
    custom_format: CustomFormatTestDefinition
    indexer: str | None = Field(default=None, max_length=256)
    languages: list[str] = Field(default_factory=list, max_length=64)


class CustomFormatTestAllRequest(StrictCustomFormatModel):
    release_name: str = Field(min_length=1, max_length=8192)
    custom_format: CustomFormatTestDefinition | None = None
    indexer: str | None = Field(default=None, max_length=256)
    languages: list[str] = Field(default_factory=list, max_length=64)
    include_disabled: bool = False
    quality_profile_id: UUID | None = None


class CustomFormatConditionEvaluationResponse(BaseModel):
    condition_id: str
    condition_type: CustomFormatConditionType
    name: str | None = None
    matched: bool
    effective_result: bool
    required: bool
    negated: bool
    evidence: Any = None
    expected: Any = None
    reason: str | None = None
    group: str | None = None
    regex_match: str | None = None
    score_offset: int = 0


class CustomFormatEvaluationResponse(BaseModel):
    custom_format_id: str
    custom_format_name: str
    matched: bool
    raw_matched: bool
    suppressed_by: list[str] = Field(default_factory=list)
    suppressed_by_names: list[str] = Field(default_factory=list)
    conditions: list[CustomFormatConditionEvaluationResponse]
    group_results: dict[str, bool]
    score_offset: int = 0
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


class CustomFormatExportItem(StrictCustomFormatModel):
    schema_version: Literal[1]
    name: str
    description: str | None = None
    enabled: bool = True
    conditions: list[CustomFormatConditionInput]
    #: Names, rather than installation-specific ids, keep exports portable.
    supersedes: list[str] = Field(default_factory=list, max_length=500)


class CustomFormatExportBundle(StrictCustomFormatModel):
    application: Literal["Medialogue"] = "Medialogue"
    schema_version: Literal[1] = CUSTOM_FORMAT_SCHEMA_VERSION
    custom_formats: list[CustomFormatExportItem]


class CustomFormatImportBundle(StrictCustomFormatModel):
    application: Literal["Medialogue"]
    schema_version: Literal[1]
    custom_formats: list[CustomFormatExportItem] = Field(min_length=1, max_length=500)


class CustomFormatImportResponse(BaseModel):
    imported: list[CustomFormatResponse]
    count: int
