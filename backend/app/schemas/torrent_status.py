from typing import Literal

from pydantic import BaseModel


TorrentMediaState = Literal["untracked", "tracked", "backed_up", "archived", "partial", "attention"]
TorrentTrackerState = Literal["healthy", "degraded", "unavailable", "unregistered", "unknown"]


class MediaTorrentStatusResponse(BaseModel):
    state: TorrentMediaState = "untracked"
    release_count: int = 0
    tracked_release_count: int = 0
    backed_up_release_count: int = 0
    torrent_count: int = 0
    present_torrent_count: int = 0
    archived_torrent_count: int = 0
    failed_archive_count: int = 0
    tracker_state: TorrentTrackerState = "unknown"
