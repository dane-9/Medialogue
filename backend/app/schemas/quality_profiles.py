from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.domain import MediaType


class QualityDefinitionResponse(BaseModel):
    id: UUID
    name: str
    resolution: str | None
    source: str | None
    modifier: str | None
    scan_type: str | None
    rank: int
    enabled: bool


class QualityProfileScoreInput(BaseModel):
    custom_format_id: UUID
    score: int = Field(ge=-1_000_000, le=1_000_000)


class QualityProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    minimum_quality_definition_id: UUID | None = None
    minimum_custom_format_score: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    quality_definition_ids: list[UUID] | None = Field(default=None, max_length=1000)
    custom_format_scores: list[QualityProfileScoreInput] = Field(default_factory=list, max_length=1000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class QualityProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    minimum_quality_definition_id: UUID | None = None
    minimum_custom_format_score: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    quality_definition_ids: list[UUID] | None = Field(default=None, max_length=1000)
    custom_format_scores: list[QualityProfileScoreInput] | None = Field(default=None, max_length=1000)
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value


class QualityProfileScoreResponse(BaseModel):
    custom_format_id: UUID
    custom_format_name: str
    score: int
    enabled: bool


class QualityProfileResponse(BaseModel):
    id: UUID
    name: str
    minimum_quality_definition: QualityDefinitionResponse | None
    minimum_custom_format_score: int | None
    qualities: list[QualityDefinitionResponse]
    custom_format_scores: list[QualityProfileScoreResponse]
    assigned_titles: int
    #: The seeded profile every title without its own falls back to. It is
    #: renameable, so callers must not identify it by name.
    is_default: bool = False
    revision: int
    created_at: datetime
    updated_at: datetime


class MediaProfileSettingsUpdate(BaseModel):
    quality_profile_id: UUID | None = None
    minimum_quality_definition_override_id: UUID | None = None
    minimum_custom_format_score_override: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    custom_format_score_overrides: dict[UUID, int] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=0)

    @field_validator("custom_format_score_overrides")
    @classmethod
    def validate_scores(cls, value: dict[UUID, int]) -> dict[UUID, int]:
        for score in value.values():
            if score < -1_000_000 or score > 1_000_000:
                raise ValueError("Custom Format override scores must be between -1000000 and 1000000")
        return value


class EffectiveCustomFormatScore(BaseModel):
    custom_format_id: UUID
    custom_format_name: str
    profile_score: int
    override_score: int | None
    effective_score: int
    enabled: bool


class MediaProfileSettingsResponse(BaseModel):
    media_type: MediaType
    entity_id: UUID
    quality_profile_id: UUID | None
    quality_profile_name: str | None
    #: True when this title has no profile of its own and is using the default.
    #: The id above is then the default's, not a choice the user made here.
    quality_profile_inherited: bool = False
    minimum_quality_definition: QualityDefinitionResponse | None
    profile_minimum_quality_definition: QualityDefinitionResponse | None
    minimum_quality_overridden: bool
    minimum_custom_format_score: int | None
    profile_minimum_custom_format_score: int | None
    minimum_custom_format_score_overridden: bool
    custom_format_scores: list[EffectiveCustomFormatScore]
    revision: int
