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

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.types import JSON, Uuid

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


JSONType = JSON()


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


class DiscoveryMode(StrEnum):
    AUTOMATIC = "automatic"
    REVIEW = "review"


class NewMediaState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    IGNORED = "ignored"


class IdentityState(StrEnum):
    MATCHED = "matched"
    MANUAL = "manual"
    UNMATCHED = "unmatched"


class ReleaseState(StrEnum):
    CURRENT = "current"
    MISSING = "missing"
    REPLACED = "replaced"
    REMOVED = "removed"
    DUPLICATE = "duplicate"


class PresenceState(StrEnum):
    PRESENT = "present"
    MISSING = "missing"


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


class MatchMethod(StrEnum):
    PARSER = "parser"
    MANUAL = "manual"


class TorrentArchiveState(StrEnum):
    NOT_ARCHIVED = "not_archived"
    ARCHIVED = "archived"
    FAILED = "failed"
    #: Its files were dropped from the archive because nothing on the server
    #: uses it any more; see ``prune_retired_archives``.
    PRUNED = "pruned"


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


class ProblemWorkflow(StrEnum):
    MANUAL = "manual"
    CHOICE = "choice"
    CONFIG = "config"
    WAITING = "waiting"


def classify_problem_workflow(reason: str) -> ProblemWorkflow:
    """Classify a Problem by the next user-visible step, not its source.

    Only reasons Medialogue actually raises are listed. Branches for reasons
    nothing emits made the taxonomy look richer than it is, and nothing ever
    reached them.
    """
    from app.core.problem_types import CONFIG_REASONS, DUPLICATE_REASONS, MOVIE_MATCH_REASONS, SHOW_MATCH_REASONS

    if reason in DUPLICATE_REASONS:
        return ProblemWorkflow.MANUAL
    if reason in MOVIE_MATCH_REASONS | SHOW_MATCH_REASONS:
        return ProblemWorkflow.CHOICE
    if reason in CONFIG_REASONS:
        return ProblemWorkflow.CONFIG
    return ProblemWorkflow.WAITING


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IntegrationType(StrEnum):
    QBITTORRENT = "qbittorrent"


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
    original_language: Mapped[str | None] = mapped_column(String(16))
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
    # Exact title returned by the indexer for the selected torrent. The raw
    # release name remains the filesystem/folder-derived fallback.
    tracker_title: Mapped[str | None] = mapped_column(Text)
    parsed_title: Mapped[str | None] = mapped_column(String(512))
    parsed_year: Mapped[int | None] = mapped_column(Integer)
    parsed_edition: Mapped[str | None] = mapped_column(String(128))
    manual_edition_override: Mapped[str | None] = mapped_column(String(128))
    edition_override_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    parse_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    # Copied out of parse_snapshot whenever that is set, so the library list can
    # read it for every release without loading the snapshots.
    identity_confidence: Mapped[float | None] = mapped_column(Float)
    selection_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    movie: Mapped[Movie] = relationship(back_populates="releases")
    quality_definition: Mapped["QualityDefinition | None"] = relationship(back_populates="movie_releases")
    directories: Mapped[list["MediaDirectory"]] = relationship(back_populates="movie_release")
    torrents: Mapped[list["Torrent"]] = relationship(secondary="movie_release_torrents", back_populates="movie_releases")

    @validates("parse_snapshot")
    def _copy_identity_confidence(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        confidence = (value or {}).get("identity_confidence")
        self.identity_confidence = float(confidence) if confidence is not None else None
        return value


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
    tmdb_episode_group_id: Mapped[str | None] = mapped_column(String(64))
    overview: Mapped[str | None] = mapped_column(Text)
    poster_ref: Mapped[str | None] = mapped_column(String(1024))
    original_language: Mapped[str | None] = mapped_column(String(16))
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
    counted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
    tracker_title: Mapped[str | None] = mapped_column(Text)
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
    #: What a scan does with media it has never seen. The API defaults to adding
    #: it, which is what a caller that says nothing means; the UI offers New Media
    #: as its default instead, and says so explicitly on every request.
    discovery_mode: Mapped[DiscoveryMode] = mapped_column(SAEnum(DiscoveryMode, native_enum=False), default=DiscoveryMode.AUTOMATIC, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Number of consecutive successful root scans on which a known directory
    # may be absent before it is considered Missing.  A root outage never
    # increments this counter.
    missing_grace_checks: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    last_health: Mapped[str | None] = mapped_column(String(32))
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    directories: Mapped[list["MediaDirectory"]] = relationship(back_populates="storage_root")
    mappings: Mapped[list["RemotePathMapping"]] = relationship(back_populates="storage_root")


class MediaDiscovery(TimestampMixin, Base):
    """A detected media directory waiting for an explicit library decision."""

    __tablename__ = "media_discoveries"
    __table_args__ = (
        UniqueConstraint("media_directory_id", name="uq_media_discoveries_directory"),
        Index("ix_media_discoveries_new_media_state", "new_media_state"),
        Index("ix_media_discoveries_type_new_media_state", "media_type", "new_media_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_directory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_directories.id", ondelete="CASCADE"), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, native_enum=False), nullable=False)
    new_media_state: Mapped[NewMediaState] = mapped_column(SAEnum(NewMediaState, native_enum=False), default=NewMediaState.PENDING, nullable=False)
    parsed_title: Mapped[str | None] = mapped_column(String(512))
    parsed_year: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    suggested_tmdb_id: Mapped[int | None] = mapped_column(Integer)
    selected_tmdb_id: Mapped[int | None] = mapped_column(Integer)
    manual_edition_override: Mapped[str | None] = mapped_column(String(128))
    edition_override_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Quality Profile to give the title this directory becomes. Unconstrained on
    #: purpose: a profile deleted between approval and adding must not prevent
    #: the directory from being added, so an unknown id reads back as "no profile".
    quality_profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    parse_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    approved_media_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    media_directory: Mapped["MediaDirectory"] = relationship(back_populates="discovery")


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
    discovery: Mapped["MediaDiscovery | None"] = relationship(back_populates="media_directory", cascade="all, delete-orphan", uselist=False)


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
    #: When Medialogue last put this torrent back into qBittorrent from the
    #: archive on its own. Set once: a torrent removed again after that is left
    #: to the user.
    auto_readded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Where that automatic re-add stands: "checking" while qBittorrent verifies
    #: the files, "seeding" once they were complete and it was started,
    #: "files_missing" when the check came up short (left stopped), "failed"
    #: when qBittorrent could not take it.
    readd_state: Mapped[str | None] = mapped_column(String(16))

    observations: Mapped[list["TorrentClientObservation"]] = relationship(back_populates="torrent", cascade="all, delete-orphan")
    tracker_observations: Mapped[list["TorrentTrackerObservation"]] = relationship(back_populates="torrent", cascade="all, delete-orphan")
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
    download_speed: Mapped[int | None] = mapped_column(BigInteger)
    upload_speed: Mapped[int | None] = mapped_column(BigInteger)
    eta_seconds: Mapped[int | None] = mapped_column(Integer)
    amount_left: Mapped[int | None] = mapped_column(BigInteger)
    ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    #: Peers this client is connected to. ``*_total`` is the whole swarm as the
    #: tracker reports it, which is what qBittorrent renders in parentheses.
    seeds: Mapped[int | None] = mapped_column(Integer)
    seeds_total: Mapped[int | None] = mapped_column(Integer)
    leeches: Mapped[int | None] = mapped_column(Integer)
    leeches_total: Mapped[int | None] = mapped_column(Integer)
    availability: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    category: Mapped[str | None] = mapped_column(String(256))
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Since when the swarm has reported no seeder besides this client. Cleared
    #: as soon as another seeder shows up; unknown counts leave it unchanged.
    alone_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    torrent: Mapped[Torrent] = relationship(back_populates="observations")
    download_client: Mapped["DownloadClient"] = relationship(back_populates="observations")


class TorrentTrackerObservation(Base):
    """Redacted, durable announce health for one torrent tracker endpoint."""

    __tablename__ = "torrent_tracker_observations"
    __table_args__ = (
        UniqueConstraint("torrent_id", "download_client_id", "endpoint_key", name="uq_torrent_tracker_endpoint"),
        Index("ix_torrent_tracker_active_state", "torrent_id", "active", "health_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    torrent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("torrents.id", ondelete="CASCADE"), nullable=False)
    # Deliberately not a foreign key: tracker evidence must survive removal of
    # a qBittorrent client configuration.
    download_client_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    endpoint_key: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_display: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    health_state: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    message_key: Mapped[str | None] = mapped_column(String(64))
    peers: Mapped[int | None] = mapped_column(Integer)
    seeds: Mapped[int | None] = mapped_column(Integer)
    leeches: Mapped[int | None] = mapped_column(Integer)
    downloaded: Mapped[int | None] = mapped_column(Integer)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    terminal_failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    terminal_reason: Mapped[str | None] = mapped_column(String(64))
    terminal_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    torrent: Mapped[Torrent] = relationship(back_populates="tracker_observations")


class ReleaseBlocklist(Base):
    """A removable policy entry created from an immutable download failure."""

    __tablename__ = "release_blocklist"
    __table_args__ = (
        Index("ix_release_blocklist_created", "created_at"),
        Index("ix_release_blocklist_target", "target_entity_type", "target_entity_id"),
        Index("ix_release_blocklist_hash", "info_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, native_enum=False), nullable=False)
    target_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    torrent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("torrents.id", ondelete="SET NULL"))
    info_hash: Mapped[str | None] = mapped_column(String(128))
    source_title: Mapped[str] = mapped_column(Text, nullable=False)
    indexer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("indexers.id", ondelete="SET NULL"))
    indexer_name: Mapped[str | None] = mapped_column(String(256))
    quality: Mapped[str | None] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    failure_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


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
    """Runtime state for a file-configured qBittorrent client.

    Connection settings and credentials are persisted under ``/config``; this
    table retains only health/observation state and a stable UUID referenced by
    torrent observations and search history.
    """

    __tablename__ = "download_clients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    health: Mapped[str | None] = mapped_column(String(32))
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CustomFormat(TimestampMixin, Base):
    __tablename__ = "custom_formats"
    __table_args__ = (UniqueConstraint("builtin_key", name="uq_custom_formats_builtin_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    condition_definition: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    builtin_key: Mapped[str | None] = mapped_column(String(64))


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


class ConfirmationNonce(Base):
    """Durable single-use IDs for destructive confirmation tokens."""

    __tablename__ = "confirmation_nonces"
    __table_args__ = (Index("ix_confirmation_nonces_expires_at", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jti: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_created", "created_at"),
        Index(
            "uq_jobs_active_schedule",
            "schedule_id",
            unique=True,
            sqlite_where=text("schedule_id IS NOT NULL AND status IN ('QUEUED', 'RUNNING')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
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
        Index("ix_problems_status_workflow_created", "status", "workflow", "created_at"),
        Index("ix_problems_reason", "reason"),
        # Exactly one durable OPEN problem may represent a given condition.
        # Separate partial indexes are required because SQLite treats NULL
        # values as distinct in ordinary UNIQUE indexes.
        Index(
            "uq_problems_open_entity",
            "reason",
            "entity_type",
            "entity_id",
            unique=True,
            sqlite_where=text("status = 'OPEN' AND entity_id IS NOT NULL"),
        ),
        Index(
            "uq_problems_open_global",
            "reason",
            "entity_type",
            unique=True,
            sqlite_where=text("status = 'OPEN' AND entity_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[ProblemStatus] = mapped_column(SAEnum(ProblemStatus, native_enum=False), default=ProblemStatus.OPEN, nullable=False)
    workflow: Mapped[ProblemWorkflow] = mapped_column(
        SAEnum(ProblemWorkflow, native_enum=False), default=ProblemWorkflow.WAITING, nullable=False
    )
    severity: Mapped[Severity] = mapped_column(SAEnum(Severity, native_enum=False), default=Severity.WARNING, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Indexer(TimestampMixin, Base):
    """Runtime health state for an indexer configured under ``/config``."""

    __tablename__ = "indexers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    health: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)


class LibraryHistoryEntry(Base):
    """A movie, or a show's seasons, that left the library.

    Written when Medialogue removes a title, so what you once had is still on
    record after the title's own rows are gone. Plain values only: nothing
    here points at rows that no longer exist.
    """

    __tablename__ = "library_history"
    __table_args__ = (Index("ix_library_history_removed_at", "removed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, native_enum=False), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    poster_ref: Mapped[str | None] = mapped_column(Text)
    #: The seasons removed, for a show; empty when the whole show went.
    season_numbers: Mapped[list[int]] = mapped_column(JSONType, default=list, nullable=False)
    #: What was on disk when it went.
    release_name: Mapped[str | None] = mapped_column(Text)
    quality: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[int | None] = mapped_column(BigInteger)
    #: How it went, in words: "Deleted from Downloads", ...
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    removed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ReplacementReview(TimestampMixin, Base):
    """What the user decided about one Replace Media title, and its last search.

    Keyed by the title's target key rather than by torrent, because a title with
    no torrent at all can be listed and ignored too. Ignoring is tied to the
    reason it was listed for, so a new problem brings the title back.
    """

    __tablename__ = "replacement_reviews"

    target_key: Mapped[str] = mapped_column(String(150), primary_key=True)
    ignored_reason: Mapped[str | None] = mapped_column(String(32))
    last_search_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_search_result: Mapped[str | None] = mapped_column(String(32))


class UpgradeCandidate(TimestampMixin, Base):
    """Best currently known manual upgrade candidate for one library target.

    Movie targets are release-level. Show targets are season-level because the
    upgrade policy intentionally prefers one season pack over episode or
    multi-season replacements. The candidate keeps the private download URL for
    the later explicit Upgrade action, but API responses must never expose it.
    """

    __tablename__ = "upgrade_candidates"
    __table_args__ = (
        UniqueConstraint("target_key", name="uq_upgrade_candidates_target_key"),
        Index("ix_upgrade_candidates_media_type", "media_type"),
        Index("ix_upgrade_candidates_execution", "execution_eligible", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_key: Mapped[str] = mapped_column(String(160), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, native_enum=False), nullable=False)
    movie_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    season_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    current_release_ids: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    current_release_name: Mapped[str] = mapped_column(Text, nullable=False)
    current_quality: Mapped[str | None] = mapped_column(String(256))
    current_custom_format_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    indexer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("indexers.id", ondelete="SET NULL"))
    indexer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    guid: Mapped[str] = mapped_column(Text, nullable=False)
    download_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    quality: Mapped[str | None] = mapped_column(String(256))
    custom_format_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score_delta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    size: Mapped[int | None] = mapped_column(BigInteger)
    seeders: Mapped[int | None] = mapped_column(Integer)
    leechers: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upgrade_types: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    added_custom_formats: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    execution_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    execution_block_reason: Mapped[str | None] = mapped_column(Text)
    comparison_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    # Complete private result set from the latest target search. Download URLs
    # stay here and are projected through a URL-free response schema.
    search_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    execution_state: Mapped[str | None] = mapped_column(String(40))
    submitted_info_hash: Mapped[str | None] = mapped_column(String(64))
    selected_download_client_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    selected_download_client_name: Mapped[str | None] = mapped_column(String(256))
    selected_category: Mapped[str | None] = mapped_column(String(256))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    torrent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    incoming_release_ids: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    old_torrents_removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_error: Mapped[str | None] = mapped_column(Text)
    last_searched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


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
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    guid: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    download_url: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int | None] = mapped_column(BigInteger)
    seeders: Mapped[int | None] = mapped_column(Integer)
    leechers: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parse_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    quality: Mapped[str | None] = mapped_column(String(256))
    edition: Mapped[str | None] = mapped_column(String(128))
    release_group: Mapped[str | None] = mapped_column(String(256))
    custom_format_score: Mapped[int | None] = mapped_column(Integer)
    custom_format_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Correlates the selected tracker result with the torrent qBittorrent later
    # reports even when its internal name differs from the indexer listing.
    submitted_info_hash: Mapped[str | None] = mapped_column(String(64), index=True)
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
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_duration_ms: Mapped[int | None] = mapped_column(Integer)


class TMDBConfiguration(TimestampMixin, Base):
    """TMDB runtime health state; API key/settings live under ``/config``."""

    __tablename__ = "tmdb_configurations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    health: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)


class PlexConfiguration(TimestampMixin, Base):
    """Plex runtime health state; URL/token/settings live under ``/config``."""

    __tablename__ = "plex_configurations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    health: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    machine_identifier: Mapped[str | None] = mapped_column(String(256))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)


class UiSelection(Base):
    """A row left out of a list's bulk action (New Media, Upgrade, Replace).

    Kept on the server, not in one browser, so unticking a row sticks across
    reloads and devices. ``item_id`` is the row's id in that list.
    """

    __tablename__ = "ui_selections"

    list_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
