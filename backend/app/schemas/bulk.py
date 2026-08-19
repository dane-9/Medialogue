from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class MovieBulkAction(str, Enum):
    CHANGE_PROFILE = "change_profile"
    ADD_TAGS = "add_tags"
    REMOVE_TAGS = "remove_tags"
    MONITOR = "monitor"
    UNMONITOR = "unmonitor"
    RECHECK_PLEX = "recheck_plex"
    REEVALUATE_PARSER = "reevaluate_parser"
    REEVALUATE_CUSTOM_FORMATS = "reevaluate_custom_formats"


class MovieBulkRequest(BaseModel):
    movie_ids: list[str] = Field(min_length=1, max_length=1000)
    action: MovieBulkAction
    quality_profile_id: UUID | None = None
    tag_ids: list[UUID] = Field(default_factory=list, max_length=250)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "MovieBulkRequest":
        if self.action in {MovieBulkAction.ADD_TAGS, MovieBulkAction.REMOVE_TAGS} and not self.tag_ids:
            raise ValueError("At least one tag_id is required for tag actions.")
        return self


class MovieBulkResponse(BaseModel):
    action: MovieBulkAction
    requested: int
    updated: int
    movie_ids: list[str]
    details: dict[str, object] = Field(default_factory=dict)
