from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


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
    quality_definition_ids: list[UUID] | None = Field(default=None, max_length=1000)
    custom_format_scores: list[QualityProfileScoreInput] = Field(default_factory=list, max_length=1000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class QualityProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    minimum_quality_definition_id: UUID | None = None
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
    qualities: list[QualityDefinitionResponse]
    custom_format_scores: list[QualityProfileScoreResponse]
    assigned_titles: int
    revision: int
    created_at: datetime
    updated_at: datetime


class MediaProfileSettingsUpdate(BaseModel):
    quality_profile_id: UUID | None = None
    minimum_quality_definition_override_id: UUID | None = None
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
    media_type: str
    entity_id: UUID
    quality_profile_id: UUID | None
    quality_profile_name: str | None
    minimum_quality_definition: QualityDefinitionResponse | None
    profile_minimum_quality_definition: QualityDefinitionResponse | None
    minimum_quality_overridden: bool
    custom_format_scores: list[EffectiveCustomFormatScore]
    revision: int


class ScoreBreakdownItem(BaseModel):
    custom_format_id: str
    custom_format_name: str
    matched: bool
    profile_score: int
    override_score: int | None
    effective_score: int
    contribution: int
    conditions: list[dict[str, Any]] = Field(default_factory=list)


class SearchScoreSnapshot(BaseModel):
    schema_version: int = 1
    profile_id: UUID | None
    profile_name: str | None
    profile_revision: int | None
    assignment_revision: int | None
    minimum_quality: str | None
    minimum_quality_definition_id: UUID | None
    candidate_quality: str | None
    minimum_quality_met: bool | None
    total_score: int
    breakdown: list[ScoreBreakdownItem]
