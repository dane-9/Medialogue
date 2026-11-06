from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ReplacementTab = Literal["needs_search", "found", "in_progress", "dismissed"]


class ReplacementItem(BaseModel):
    target_key: str
    title: str
    reason: str
    torrent_ids: list[str] = Field(default_factory=list)
    tab: ReplacementTab
    #: Needs a decision from the user (search/choose, or a finished handoff).
    needs_attention: bool
    dismissed: bool
    media_type: Literal["movies", "shows"] | None = None
    movie_id: str | None = None
    show_id: str | None = None
    season_id: str | None = None
    season_number: int | None = None
    year: int | None = None
    poster_ref: str | None = None
    current_release: str | None = None
    current_quality: str | None = None
    current_score: int | None = None
    media_present: bool | None = None
    last_searched_at: datetime | None = None
    last_search_result: Literal["candidate", "no_match", "failed"] | None = None
    candidate_id: str | None = None
    candidate_title: str | None = None
    candidate_quality: str | None = None
    candidate_score: int | None = None
    candidate_size: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    indexer: str | None = None
    indexer_id: str | None = None
    #: The indexer's badge colour from its configuration, as search results use.
    indexer_color: str | None = None
    execution_state: str | None = None
    error: str | None = None
    blocked_reason: str | None = None
    #: A replacement search covering this item is queued or running.
    searching: bool = False


class ReplacementQueueResponse(BaseModel):
    items: list[ReplacementItem]
    counts: dict[ReplacementTab, int]


class ReplacementCountResponse(BaseModel):
    count: int


class ReplacementReviewResponse(BaseModel):
    updated: int


class ReplacementRetireResponse(BaseModel):
    state: str
    error: str | None = None


class ReplacementFinishResponse(BaseModel):
    state: str
    old_media_retained: bool
