from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.upgrades import UpgradeMatchedFormat


ReplacementTab = Literal["needs_search", "found", "in_progress", "dismissed"]


class LostSourceEvidence(BaseModel):
    """Why one torrent behind a title counts as lost: its tracker's own words."""

    torrent_name: str
    tracker: str | None = None
    message: str | None = None
    #: The last time this tracker answered normally, when it ever did.
    last_worked_at: datetime | None = None


ReplacementReason = Literal["not_watched", "files_missing", "missing", "no_seeders", "tracker_gone", "removed_from_tracker", "no_torrent"]


class ReplacementItem(BaseModel):
    target_key: str
    title: str
    #: Why the title cannot be restored, in words, and as a code for the page.
    reason: str
    #: None only for a replacement still under way after its problem cleared.
    reason_code: ReplacementReason | None = None
    #: When the problem started, where that is known.
    reason_since: datetime | None = None
    #: Not in qBittorrent, with archived torrents that can be added back.
    readdable: bool = False
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
    #: The lost release's size, from its torrents, so the size change shows.
    current_size: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    indexer: str | None = None
    indexer_id: str | None = None
    #: The indexer's badge colour from its configuration, as search results use.
    indexer_color: str | None = None
    execution_state: str | None = None
    error: str | None = None
    #: A replacement search covering this item is queued or running.
    searching: bool = False
    #: The media folder the title lives in, so the page groups by folder as
    #: New Media and Upgrade Media do. None when it is not in the library.
    root_name: str | None = None
    #: One entry per lost torrent, so the page can say why the title is here.
    lost_evidence: list[LostSourceEvidence] = Field(default_factory=list)
    #: The Custom Formats behind each score, for the score popup.
    #: The quality profile that scored both releases.
    profile_name: str | None = None
    current_matched_formats: list[UpgradeMatchedFormat] = Field(default_factory=list)
    candidate_matched_formats: list[UpgradeMatchedFormat] = Field(default_factory=list)
    #: Why the replacement is better: quality, custom_format, season_pack, proper, ...
    upgrade_types: list[str] = Field(default_factory=list)
    #: Live from the download client while the replacement downloads, as
    #: Upgrade Media shows it: a 0..1 share, bytes per second, seconds left.
    download_progress: float | None = None
    download_speed: int | None = None
    download_eta_seconds: int | None = None
    #: Where the current release came from: the indexer when Medialogue sent
    #: it, otherwise the tracker host its torrent announces to.
    current_indexer: str | None = None
    current_indexer_color: str | None = None
    current_tracker: str | None = None


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
