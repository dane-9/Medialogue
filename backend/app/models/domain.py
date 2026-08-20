"""Core durable domain model.

These models intentionally describe observations and history, not an import
queue.  In particular, a MediaDirectory retains its path when it disappears,
and Torrent rows are independent of logical title deletion so recovery history
is not accidentally lost.
"""

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


JSONType = JSON().with_variant(JSONB(), "postgresql")


class StrEnum(str, enum.Enum):
    pass


class MediaType(StrEnum):
    MOVIES = "movies"
    SHOWS = "shows"


class MediaScope(StrEnum):
    MOVIES = "movies"
    SHOWS = "shows"
    BOTH = "both"


class AccessMode(StrEnum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class IdentityState(StrEnum):
    MATCHED = "matched"
    MANUAL = "manual"
    UNCERTAIN = "uncertain"
    CONFLICT = "conflict"
    UNMATCHED = "unmatched"


class ReleaseState(StrEnum):
    CURRENT = "current"
    MISSING = "missing"
    REPLACED = "replaced"
    REMOVED = "removed"
    CONFLICT = "conflict"
    DUPLICATE = "duplicate"


class PresenceState(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    UNMATCHED = "unmatched"
    CONFLICT = "conflict"


class ReleaseScope(StrEnum):
    EPISODE = "episode"
    MULTI_EPISODE = "multi_episode"
    SEASON_PACK = "season_pack"
    OTHER = "other"


class MediaRole(StrEnum):
    MOVIE_VIDEO = "movie_video"
    EPISODE_VIDEO = "episode_video"
    MULTI_EPISODE_VIDEO = "multi_episode_video"
    DVD_STRUCTURE = "dvd_structure"
    BLURAY_STRUCTURE = "bluray_structure"
    OTHER_MEDIA = "other_media"


class SourceType(StrEnum):
    FILESYSTEM = "filesystem"
    TORRENT_NAME = "torrent_name"
    DIRECTORY_NAME = "directory_name"
    FILENAME = "filename"
    PROWLARR_RESULT = "prowlarr_result"


class MatchMethod(StrEnum):
    PARSER = "parser"
    PLEX = "plex"
    MANUAL = "manual"
    COMBINED = "combined"


class TorrentArchiveState(StrEnum):
    NOT_ARCHIVED = "not_archived"
    ARCHIVED = "archived"
    FAILED = "failed"


class AssociationType(StrEnum):
    INCOMING = "incoming"
    ATTACHED = "attached"
    HISTORICAL = "historical"


class PlexMatchState(StrEnum):
    MATCHED = "matched"
    NOT_FOUND = "not_found"
    PENDING = "pending"
    CONFLICT = "conflict"
    MULTIPLE_VERSIONS = "multiple_versions"
    UNAVAILABLE = "unavailable"


class PlexMatchMethod(StrEnum):
    EXACT_PATH = "exact_path"
    TITLE_YEAR = "title_year"
    MANUAL = "manual"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ProblemStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IntegrationType(StrEnum):
    QBITTORRENT = "qbittorrent"
    PLEX = "plex"
    PROWLARR = "prowlarr"
    TMDB = "tmdb"
    TVDB = "tvdb"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Movie(TimestampMixin, Base):
    __tablename__ = "movies"
    __table_args__ = (
        UniqueConstraint("tmdb_id", name="uq_movies_tmdb_id"),
        Index("ix_movies_tmdb_id", "tmdb_id"),
        Index("ix_movies_state", "identity_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    sort_title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    year: Mapped[int | None] = mapped_column(Integer)
    tmdb_id: Mapped[int | None] = mapped_column()
    tvdb_id: Mapped[int | None] = mapped_column()
    overview: Mapped[str | None] = mapped_column(Text)
    poster_ref: Mapped[str | None] = mapped_column(String(1024))
    monitored: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    identity_state: Mapped[IdentityState] = mapped_column(SAEnum(IdentityState, native_enum=False), default=IdentityState.UNMATCHED, nullable=False)
    manual_identity_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    releases: Mapped[list["MovieRelease"]] = relationship(back_populates="movie", cascade="all, delete-orphan")
    tags: Mapped[list["Tag"]] = relationship(secondary="movie_tags", back_populates="movies")
    plex_observations: Mapped[list["PlexObservation"]] = relationship(back_populates="movie")


class MovieRelease(TimestampMixin, Base):
    __tablename__ = "movie_releases"
    __table_args__ = (Index("ix_movie_releases_movie_state", "movie_id", "release_state"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    movie_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), nullable=False)
    raw_release_name: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_title: Mapped[str | None] = mapped_column(String(512))
    parsed_year: Mapped[int | None] = mapped_column(Integer)
    parsed_edition: Mapped[str | None] = mapped_column(String(128))
    manual_edition_override: Mapped[str | None] = mapped_column(String(128))
    effective_edition: Mapped[str | None] = mapped_column(String(128))
    quality_definition_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quality_definitions.id", ondelete="SET NULL"))
    release_group: Mapped[str | None] = mapped_column(String(256))
    release_state: Mapped[ReleaseState] = mapped_column(SAEnum(ReleaseState, native_enum=False), default=ReleaseState.CURRENT, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    became_current_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_custom_format_score: Mapped[int | None] = mapped_column(Integer)
    current_custom_format_score: Mapped[int | None] = mapped_column(Integer)
    parser_version: Mapped[str | None] = mapped_column(String(64))
    parse_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    selection_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    movie: Mapped[Movie] = relationship(back_populates="releases")
    quality_definition: Mapped["QualityDefinition | None"] = relationship(back_populates="movie_releases")
    directories: Mapped[list["MediaDirectory"]] = relationship(back_populates="movie_release")
    torrents: Mapped[list["Torrent"]] = relationship(secondary="movie_release_torrents", back_populates="movie_releases")


class Show(TimestampMixin, Base):
    __tablename__ = "shows"
    __table_args__ = (
        UniqueConstraint("tmdb_id", name="uq_shows_tmdb_id"),
        Index("ix_shows_tmdb_id", "tmdb_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    tmdb_id: Mapped[int | None] = mapped_column()
    tvdb_id: Mapped[int | None] = mapped_column()
    overview: Mapped[str | None] = mapped_column(Text)
    poster_ref: Mapped[str | None] = mapped_column(String(1024))
    monitored: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    identity_state: Mapped[IdentityState] = mapped_column(SAEnum(IdentityState, native_enum=False), default=IdentityState.UNMATCHED, nullable=False)
    manual_identity_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    seasons: Mapped[list["Season"]] = relationship(back_populates="show", cascade="all, delete-orphan")
    episodes: Mapped[list["Episode"]] = relationship(back_populates="show", cascade="all, delete-orphan")
    releases: Mapped[list["ShowRelease"]] = relationship(back_populates="show", cascade="all, delete-orphan")
    plex_observations: Mapped[list["PlexObservation"]] = relationship(back_populates="show")


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("show_id", "season_number", name="uq_seasons_show_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    show_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"), nullable=False)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    monitored: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    show: Mapped[Show] = relationship(back_populates="seasons")
    episodes: Mapped[list["Episode"]] = relationship(back_populates="season", cascade="all, delete-orphan")
    releases: Mapped[list["ShowRelease"]] = relationship(back_populates="season")


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("show_id", "season_number", "episode_number", name="uq_episodes_show_season_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    show_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"), nullable=False)
    season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    air_date: Mapped[date | None] = mapped_column(Date)
    tmdb_id: Mapped[int | None] = mapped_column()
    tvdb_id: Mapped[int | None] = mapped_column()
    monitored: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    presence_state: Mapped[PresenceState] = mapped_column(SAEnum(PresenceState, native_enum=False), default=PresenceState.MISSING, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    show: Mapped[Show] = relationship(back_populates="episodes")
    season: Mapped[Season] = relationship(back_populates="episodes")
    media_maps: Mapped[list["EpisodeMediaMap"]] = relationship(back_populates="episode", cascade="all, delete-orphan")
    plex_observations: Mapped[list["PlexObservation"]] = relationship(back_populates="episode")


class ShowRelease(TimestampMixin, Base):
    __tablename__ = "show_releases"
    __table_args__ = (Index("ix_show_releases_show_state", "show_id", "release_state"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    show_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"), nullable=False)
    season_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seasons.id", ondelete="SET NULL"))
    raw_release_name: Mapped[str] = mapped_column(Text, nullable=False)
    release_scope: Mapped[ReleaseScope] = mapped_column(SAEnum(ReleaseScope, native_enum=False), default=ReleaseScope.OTHER, nullable=False)
    quality_definition_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quality_definitions.id", ondelete="SET NULL"))
    release_group: Mapped[str | None] = mapped_column(String(256))
    release_state: Mapped[ReleaseState] = mapped_column(SAEnum(ReleaseState, native_enum=False), default=ReleaseState.CURRENT, nullable=False)
    parse_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    original_custom_format_score: Mapped[int | None] = mapped_column(Integer)
    current_custom_format_score: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    show: Mapped[Show] = relationship(back_populates="releases")
    season: Mapped[Season | None] = relationship(back_populates="releases")
    quality_definition: Mapped["QualityDefinition | None"] = relationship(back_populates="show_releases")
    directories: Mapped[list["MediaDirectory"]] = relationship(back_populates="show_release")
    torrents: Mapped[list["Torrent"]] = relationship(secondary="show_release_torrents", back_populates="show_releases")
    episode_maps: Mapped[list["EpisodeMediaMap"]] = relationship(back_populates="show_release")


class StorageRoot(TimestampMixin, Base):
    __tablename__ = "storage_roots"
    __table_args__ = (UniqueConstraint("name", name="uq_storage_roots_name"), Index("ix_storage_roots_path", "resolved_root_path"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    resolved_root_path: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, native_enum=False), nullable=False)
    access_mode: Mapped[AccessMode] = mapped_column(SAEnum(AccessMode, native_enum=False), default=AccessMode.READ_ONLY, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Number of consecutive successful root scans on which a known directory
    # may be absent before it is considered Missing.  A root outage never
    # increments this counter.
    missing_grace_checks: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    last_health: Mapped[str | None] = mapped_column(String(32))
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    directories: Mapped[list["MediaDirectory"]] = relationship(back_populates="storage_root")
    mappings: Mapped[list["RemotePathMapping"]] = relationship(back_populates="storage_root")


class RemotePathMapping(TimestampMixin, Base):
    __tablename__ = "remote_path_mappings"
    __table_args__ = (Index("ix_remote_path_mappings_integration", "integration_type", "integration_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    integration_type: Mapped[IntegrationType] = mapped_column(SAEnum(IntegrationType, native_enum=False), nullable=False)
    integration_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    remote_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    local_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    storage_root_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("storage_roots.id", ondelete="SET NULL"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    storage_root: Mapped[StorageRoot | None] = relationship(back_populates="mappings")


class MediaDirectory(Base):
    __tablename__ = "media_directories"
    __table_args__ = (Index("ix_media_directories_resolved_path", "resolved_path"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_root_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("storage_roots.id", ondelete="SET NULL"))
    reported_path: Mapped[str | None] = mapped_column(Text)
    resolved_path: Mapped[str] = mapped_column(Text, nullable=False)
    path_mapping_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("remote_path_mappings.id", ondelete="SET NULL"))
    movie_release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movie_releases.id", ondelete="SET NULL"))
    show_release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("show_releases.id", ondelete="SET NULL"))
    exists: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_exists_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missing_check_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(SAEnum(SourceType, native_enum=False), default=SourceType.FILESYSTEM, nullable=False)
    source_integration_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    storage_root: Mapped[StorageRoot | None] = relationship(back_populates="directories")
    movie_release: Mapped[MovieRelease | None] = relationship(back_populates="directories")
    show_release: Mapped[ShowRelease | None] = relationship(back_populates="directories")
    files: Mapped[list["MediaFile"]] = relationship(back_populates="media_directory", cascade="all, delete-orphan")


class MediaFile(Base):
    __tablename__ = "media_files"
    __table_args__ = (UniqueConstraint("media_directory_id", "relative_path", name="uq_media_files_directory_relative"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_directory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_directories.id", ondelete="CASCADE"), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    media_role: Mapped[MediaRole] = mapped_column(SAEnum(MediaRole, native_enum=False), default=MediaRole.OTHER_MEDIA, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    exists: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_exists_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missing_check_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    media_directory: Mapped[MediaDirectory] = relationship(back_populates="files")
    episode_maps: Mapped[list["EpisodeMediaMap"]] = relationship(back_populates="media_file", cascade="all, delete-orphan")


class EpisodeMediaMap(Base):
    __tablename__ = "episode_media_maps"
    __table_args__ = (UniqueConstraint("episode_id", "media_file_id", name="uq_episode_media_map"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    episode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False)
    media_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False)
    show_release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("show_releases.id", ondelete="SET NULL"))
    match_method: Mapped[MatchMethod] = mapped_column(SAEnum(MatchMethod, native_enum=False), default=MatchMethod.PARSER, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    episode: Mapped[Episode] = relationship(back_populates="media_maps")
    media_file: Mapped[MediaFile] = relationship(back_populates="episode_maps")
    show_release: Mapped[ShowRelease | None] = relationship(back_populates="episode_maps")


class Torrent(TimestampMixin, Base):
    __tablename__ = "torrents"
    __table_args__ = (UniqueConstraint("info_hash", name="uq_torrents_info_hash"), Index("ix_torrents_archive_state", "archive_state"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    info_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    total_size: Mapped[int | None] = mapped_column(BigInteger)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archive_state: Mapped[TorrentArchiveState] = mapped_column(SAEnum(TorrentArchiveState, native_enum=False), default=TorrentArchiveState.NOT_ARCHIVED, nullable=False)
    archive_path: Mapped[str | None] = mapped_column(Text)
    manifest_path: Mapped[str | None] = mapped_column(Text)
    manifest_schema_version: Mapped[int | None] = mapped_column(Integer)
    tracker_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict, nullable=False)

    observations: Mapped[list["TorrentClientObservation"]] = relationship(back_populates="torrent", cascade="all, delete-orphan")
    movie_releases: Mapped[list[MovieRelease]] = relationship(secondary="movie_release_torrents", back_populates="torrents")
    show_releases: Mapped[list[ShowRelease]] = relationship(secondary="show_release_torrents", back_populates="torrents")


class TorrentClientObservation(Base):
    __tablename__ = "torrent_client_observations"
    __table_args__ = (Index("ix_torrent_observations_client_present", "download_client_id", "is_present"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    torrent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("torrents.id", ondelete="CASCADE"), nullable=False)
    download_client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("download_clients.id", ondelete="CASCADE"), nullable=False)
    reported_save_path: Mapped[str | None] = mapped_column(Text)
    resolved_save_path: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(128))
    progress: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    category: Mapped[str | None] = mapped_column(String(256))
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    torrent: Mapped[Torrent] = relationship(back_populates="observations")
    download_client: Mapped["DownloadClient"] = relationship(back_populates="observations")


class MovieReleaseTorrent(Base):
    __tablename__ = "movie_release_torrents"
    __table_args__ = (UniqueConstraint("movie_release_id", "torrent_id", name="uq_movie_release_torrent"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    movie_release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("movie_releases.id", ondelete="CASCADE"), nullable=False)
    torrent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("torrents.id", ondelete="CASCADE"), nullable=False)
    association_type: Mapped[AssociationType] = mapped_column(SAEnum(AssociationType, native_enum=False), default=AssociationType.ATTACHED, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ShowReleaseTorrent(Base):
    __tablename__ = "show_release_torrents"
    __table_args__ = (UniqueConstraint("show_release_id", "torrent_id", name="uq_show_release_torrent"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    show_release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("show_releases.id", ondelete="CASCADE"), nullable=False)
    torrent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("torrents.id", ondelete="CASCADE"), nullable=False)
    association_type: Mapped[AssociationType] = mapped_column(SAEnum(AssociationType, native_enum=False), default=AssociationType.ATTACHED, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DownloadClient(TimestampMixin, Base):
    __tablename__ = "download_clients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(String(256))
    password: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[MediaType] = mapped_column(SAEnum(MediaType, native_enum=False), nullable=False)
    category: Mapped[str | None] = mapped_column(String(256))
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    health: Mapped[str | None] = mapped_column(String(32))
    # Incremented whenever configuration changes so clients can safely edit
    # settings without silently overwriting a concurrent update.
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Persist the intended reconciliation cadence even though this milestone
    # exposes manual polling only; a later scheduler can consume this value.
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)

    observations: Mapped[list[TorrentClientObservation]] = relationship(
        back_populates="download_client", cascade="all, delete-orphan", passive_deletes=True
    )


class PlexObservation(Base):
    __tablename__ = "plex_observations"
    __table_args__ = (
        Index("ix_plex_observations_path", "resolved_path"),
        UniqueConstraint("movie_release_id", name="uq_plex_observation_movie_release"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, native_enum=False), nullable=False)
    movie_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    show_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"))
    episode_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"))
    movie_release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movie_releases.id", ondelete="SET NULL"))
    media_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    plex_rating_key: Mapped[str | None] = mapped_column(String(256))
    plex_title: Mapped[str | None] = mapped_column(String(512))
    plex_year: Mapped[int | None] = mapped_column(Integer)
    plex_edition: Mapped[str | None] = mapped_column(String(128))
    plex_reported_path: Mapped[str | None] = mapped_column(Text)
    resolved_path: Mapped[str | None] = mapped_column(Text)
    match_state: Mapped[PlexMatchState] = mapped_column(SAEnum(PlexMatchState, native_enum=False), nullable=False)
    match_method: Mapped[PlexMatchMethod | None] = mapped_column(SAEnum(PlexMatchMethod, native_enum=False))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    movie: Mapped[Movie | None] = relationship(back_populates="plex_observations")
    show: Mapped[Show | None] = relationship(back_populates="plex_observations")
    episode: Mapped[Episode | None] = relationship(back_populates="plex_observations")


class ParseEvidence(Base):
    __tablename__ = "parse_evidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type: Mapped[SourceType] = mapped_column(SAEnum(SourceType, native_enum=False), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    parse_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CustomFormat(TimestampMixin, Base):
    __tablename__ = "custom_formats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    media_scope: Mapped[MediaScope] = mapped_column(SAEnum(MediaScope, native_enum=False), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    condition_definition: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class QualityDefinition(TimestampMixin, Base):
    __tablename__ = "quality_definitions"
    __table_args__ = (UniqueConstraint("name", name="uq_quality_definitions_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str | None] = mapped_column(String(64))
    modifier: Mapped[str | None] = mapped_column(String(64))
    scan_type: Mapped[str | None] = mapped_column(String(64))
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    parser_definition: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    movie_releases: Mapped[list[MovieRelease]] = relationship(back_populates="quality_definition")
    show_releases: Mapped[list[ShowRelease]] = relationship(back_populates="quality_definition")


class QualityProfile(TimestampMixin, Base):
    __tablename__ = "quality_profiles"
    __table_args__ = (Index("uq_quality_profiles_name", "name", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    minimum_quality_definition_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quality_definitions.id", ondelete="SET NULL"))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    minimum_quality_definition: Mapped[QualityDefinition | None] = relationship()
    custom_format_scores: Mapped[list["ProfileCustomFormatScore"]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class ProfileCustomFormatScore(Base):
    __tablename__ = "profile_custom_format_scores"
    __table_args__ = (UniqueConstraint("profile_id", "custom_format_id", name="uq_profile_custom_format"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quality_profiles.id", ondelete="CASCADE"), nullable=False)
    custom_format_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("custom_formats.id", ondelete="CASCADE"), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)

    profile: Mapped[QualityProfile] = relationship(back_populates="custom_format_scores")
    custom_format: Mapped[CustomFormat] = relationship()


class MediaProfileOverride(Base):
    __tablename__ = "media_profile_overrides"
    __table_args__ = (
        Index("ix_media_profile_overrides_movie", "movie_id"),
        Index("ix_media_profile_overrides_show", "show_id"),
        Index("uq_media_profile_override_movie", "movie_id", unique=True),
        Index("uq_media_profile_override_show", "show_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, native_enum=False), nullable=False)
    movie_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    show_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"))
    quality_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quality_profiles.id", ondelete="SET NULL"))
    override_definition: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    quality_profile: Mapped[QualityProfile | None] = relationship()


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("name", name="uq_tags_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    movies: Mapped[list[Movie]] = relationship(secondary="movie_tags", back_populates="tags")


class MovieTag(Base):
    __tablename__ = "movie_tags"
    __table_args__ = (UniqueConstraint("movie_id", "tag_id", name="uq_movie_tag"),)

    movie_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_entity_created", "entity_type", "entity_id", "created_at"), Index("ix_events_created", "created_at"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[Severity] = mapped_column(SAEnum(Severity, native_enum=False), default=Severity.INFO, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[JobStatus] = mapped_column(SAEnum(JobStatus, native_enum=False), default=JobStatus.QUEUED, nullable=False)
    progress: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    cancellable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Problem(Base):
    __tablename__ = "problems"
    __table_args__ = (
        Index("ix_problems_status_created", "status", "created_at"),
        Index("ix_problems_reason", "reason"),
        # Exactly one durable OPEN problem may represent a given condition.
        # Separate partial indexes are required because PostgreSQL/SQLite both
        # treat NULL values as distinct in ordinary UNIQUE indexes.
        Index(
            "uq_problems_open_entity",
            "reason",
            "entity_type",
            "entity_id",
            unique=True,
            postgresql_where=text("status = 'OPEN' AND entity_id IS NOT NULL"),
            sqlite_where=text("status = 'OPEN' AND entity_id IS NOT NULL"),
        ),
        Index(
            "uq_problems_open_global",
            "reason",
            "entity_type",
            unique=True,
            postgresql_where=text("status = 'OPEN' AND entity_id IS NULL"),
            sqlite_where=text("status = 'OPEN' AND entity_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[ProblemStatus] = mapped_column(SAEnum(ProblemStatus, native_enum=False), default=ProblemStatus.OPEN, nullable=False)
    severity: Mapped[Severity] = mapped_column(SAEnum(Severity, native_enum=False), default=Severity.WARNING, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Indexer(TimestampMixin, Base):
    __tablename__ = "indexers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    torznab_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[MediaScope] = mapped_column(SAEnum(MediaScope, native_enum=False), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    health: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class InteractiveSearchResult(Base):
    """Temporary Prowlarr/Torznab search result with durable selection evidence.

    Unselected rows expire after the configured search-result lifetime. Selected
    rows are retained as the immutable search-time evidence until the later
    release-history phase copies/links them to the completed release. The raw
    download URL is deliberately never returned by API schemas.
    """

    __tablename__ = "search_results"
    __table_args__ = (
        Index("ix_search_results_job_created", "job_id", "created_at"),
        Index("ix_search_results_expiry", "expires_at"),
        UniqueConstraint("job_id", "indexer_id", "guid", name="uq_search_result_job_indexer_guid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    indexer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("indexers.id", ondelete="SET NULL"))
    indexer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, native_enum=False), nullable=False)
    target_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    guid: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    download_url: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int | None] = mapped_column(BigInteger)
    seeders: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str | None] = mapped_column(String(64))
    parse_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    quality: Mapped[str | None] = mapped_column(String(256))
    edition: Mapped[str | None] = mapped_column(String(128))
    release_group: Mapped[str | None] = mapped_column(String(256))
    custom_format_score: Mapped[int | None] = mapped_column(Integer)
    custom_format_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    selected_download_client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("download_clients.id", ondelete="SET NULL"))
    selection_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Schedule(TimestampMixin, Base):
    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(128), nullable=False)
    expression: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class TMDBConfiguration(TimestampMixin, Base):
    __tablename__ = "tmdb_configurations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    health: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class PlexConfiguration(TimestampMixin, Base):
    __tablename__ = "plex_configurations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    health: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    machine_identifier: Mapped[str | None] = mapped_column(String(256))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
