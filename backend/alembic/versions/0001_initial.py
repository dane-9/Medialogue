"""Initial Medialogue schema.

This revision is intentionally explicit and immutable. Do not import ORM model
metadata here: later model changes must be represented by later migrations.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('admin_users',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('username', sa.String(length=128), nullable=False),
        sa.Column('password_hash', sa.String(length=512), nullable=False),
        sa.Column('is_default_password', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_admin_users_username', 'admin_users', ['username'], unique=True)

    op.create_table('custom_formats',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('media_scope', sa.Enum('MOVIES', 'SHOWS', 'BOTH', name='mediascope', native_enum=False), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('condition_definition', JSON_TYPE, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('download_clients',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('username', sa.String(length=256)),
        sa.Column('password', sa.Text()),
        sa.Column('scope', sa.Enum('MOVIES', 'SHOWS', name='mediatype', native_enum=False), nullable=False),
        sa.Column('category', sa.String(length=256)),
        sa.Column('tags', JSON_TYPE, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('health', sa.String(length=32)),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('poll_interval_seconds', sa.Integer(), nullable=False),
        sa.Column('last_polled_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_type', sa.String(length=128), nullable=False),
        sa.Column('severity', sa.Enum('INFO', 'WARNING', 'ERROR', name='severity', native_enum=False), nullable=False),
        sa.Column('entity_type', sa.String(length=128), nullable=False),
        sa.Column('entity_id', sa.Uuid()),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details', JSON_TYPE, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_events_created', 'events', ['created_at'], unique=False)
    op.create_index('ix_events_entity_created', 'events', ['entity_type', 'entity_id', 'created_at'], unique=False)

    op.create_table('indexers',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('torznab_url', sa.Text(), nullable=False),
        sa.Column('api_key', sa.Text()),
        sa.Column('scope', sa.Enum('MOVIES', 'SHOWS', 'BOTH', name='mediascope', native_enum=False), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('job_type', sa.String(length=128), nullable=False),
        sa.Column('status', sa.Enum('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'INTERRUPTED', name='jobstatus', native_enum=False), nullable=False),
        sa.Column('progress', JSON_TYPE, nullable=False),
        sa.Column('summary', JSON_TYPE, nullable=False),
        sa.Column('error', JSON_TYPE),
        sa.Column('cancellable', sa.Boolean(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_jobs_status_created', 'jobs', ['status', 'created_at'], unique=False)

    op.create_table('movies',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('sort_title', sa.String(length=512), nullable=False),
        sa.Column('year', sa.Integer()),
        sa.Column('tmdb_id', sa.Integer()),
        sa.Column('tvdb_id', sa.Integer()),
        sa.Column('overview', sa.Text()),
        sa.Column('poster_ref', sa.String(length=1024)),
        sa.Column('monitored', sa.Boolean(), nullable=False),
        sa.Column('identity_state', sa.Enum('MATCHED', 'MANUAL', 'UNCERTAIN', 'CONFLICT', 'UNMATCHED', name='identitystate', native_enum=False), nullable=False),
        sa.Column('manual_identity_override', sa.Boolean(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('metadata_refreshed_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tmdb_id', name='uq_movies_tmdb_id'),
    )
    op.create_index('ix_movies_sort_title', 'movies', ['sort_title'], unique=False)
    op.create_index('ix_movies_state', 'movies', ['identity_state'], unique=False)
    op.create_index('ix_movies_tmdb_id', 'movies', ['tmdb_id'], unique=False)

    op.create_table('parse_evidence',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('source_type', sa.Enum('FILESYSTEM', 'TORRENT_NAME', 'DIRECTORY_NAME', 'FILENAME', 'PROWLARR_RESULT', name='sourcetype', native_enum=False), nullable=False),
        sa.Column('source_id', sa.Uuid()),
        sa.Column('raw_name', sa.Text(), nullable=False),
        sa.Column('parse_snapshot', JSON_TYPE, nullable=False),
        sa.Column('parser_version', sa.String(length=64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('plex_configurations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('token', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('health', sa.String(length=32), nullable=False),
        sa.Column('machine_identifier', sa.String(length=256)),
        sa.Column('last_checked_at', sa.DateTime(timezone=True)),
        sa.Column('last_success_at', sa.DateTime(timezone=True)),
        sa.Column('latency_ms', sa.Integer()),
        sa.Column('last_error', sa.Text()),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('problems',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('reason', sa.String(length=128), nullable=False),
        sa.Column('status', sa.Enum('OPEN', 'RESOLVED', 'DISMISSED', name='problemstatus', native_enum=False), nullable=False),
        sa.Column('severity', sa.Enum('INFO', 'WARNING', 'ERROR', name='severity', native_enum=False), nullable=False),
        sa.Column('entity_type', sa.String(length=128), nullable=False),
        sa.Column('entity_id', sa.Uuid()),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details', JSON_TYPE, nullable=False),
        sa.Column('resolution', JSON_TYPE),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_problems_reason', 'problems', ['reason'], unique=False)
    op.create_index('ix_problems_status_created', 'problems', ['status', 'created_at'], unique=False)

    op.create_table('quality_definitions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('resolution', sa.String(length=32)),
        sa.Column('source', sa.String(length=64)),
        sa.Column('modifier', sa.String(length=64)),
        sa.Column('scan_type', sa.String(length=64)),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('parser_definition', JSON_TYPE, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_quality_definitions_name'),
    )

    op.create_table('schedules',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('job_type', sa.String(length=128), nullable=False),
        sa.Column('expression', sa.String(length=128), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('settings', JSON_TYPE, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('shows',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('year', sa.Integer()),
        sa.Column('tmdb_id', sa.Integer()),
        sa.Column('tvdb_id', sa.Integer()),
        sa.Column('overview', sa.Text()),
        sa.Column('poster_ref', sa.String(length=1024)),
        sa.Column('monitored', sa.Boolean(), nullable=False),
        sa.Column('identity_state', sa.Enum('MATCHED', 'MANUAL', 'UNCERTAIN', 'CONFLICT', 'UNMATCHED', name='identitystate', native_enum=False), nullable=False),
        sa.Column('manual_identity_override', sa.Boolean(), nullable=False),
        sa.Column('metadata_refreshed_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tmdb_id', name='uq_shows_tmdb_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_shows_tmdb_id', 'shows', ['tmdb_id'], unique=False)

    op.create_table('storage_roots',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('resolved_root_path', sa.Text(), nullable=False),
        sa.Column('media_type', sa.Enum('MOVIES', 'SHOWS', name='mediatype', native_enum=False), nullable=False),
        sa.Column('access_mode', sa.Enum('READ_ONLY', 'READ_WRITE', name='accessmode', native_enum=False), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('missing_grace_checks', sa.Integer(), nullable=False),
        sa.Column('last_health', sa.String(length=32)),
        sa.Column('last_health_checked_at', sa.DateTime(timezone=True)),
        sa.Column('last_scan_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_storage_roots_name'),
    )
    op.create_index('ix_storage_roots_path', 'storage_roots', ['resolved_root_path'], unique=False)

    op.create_table('tags',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_tags_name'),
    )

    op.create_table('tmdb_configurations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('api_key', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('health', sa.String(length=32), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(timezone=True)),
        sa.Column('last_success_at', sa.DateTime(timezone=True)),
        sa.Column('latency_ms', sa.Integer()),
        sa.Column('last_error', sa.Text()),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('torrents',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('info_hash', sa.String(length=128), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('total_size', sa.Integer()),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('archive_state', sa.Enum('NOT_ARCHIVED', 'ARCHIVED', 'FAILED', name='torrentarchivestate', native_enum=False), nullable=False),
        sa.Column('archive_path', sa.Text()),
        sa.Column('manifest_path', sa.Text()),
        sa.Column('manifest_schema_version', sa.Integer()),
        sa.Column('tracker_summary', JSON_TYPE, nullable=False),
        sa.Column('metadata', JSON_TYPE, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('info_hash', name='uq_torrents_info_hash'),
    )
    op.create_index('ix_torrents_archive_state', 'torrents', ['archive_state'], unique=False)

    op.create_table('auth_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('admin_id', sa.Uuid(), nullable=False),
        sa.Column('token_digest', sa.String(length=64), nullable=False),
        sa.Column('csrf_digest', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_digest'),
        sa.ForeignKeyConstraint(['admin_id'], ['admin_users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_auth_sessions_token_expires', 'auth_sessions', ['token_digest', 'expires_at'], unique=False)

    op.create_table('movie_releases',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('movie_id', sa.Uuid(), nullable=False),
        sa.Column('raw_release_name', sa.Text(), nullable=False),
        sa.Column('parsed_title', sa.String(length=512)),
        sa.Column('parsed_year', sa.Integer()),
        sa.Column('parsed_edition', sa.String(length=128)),
        sa.Column('manual_edition_override', sa.String(length=128)),
        sa.Column('effective_edition', sa.String(length=128)),
        sa.Column('quality_definition_id', sa.Uuid()),
        sa.Column('release_group', sa.String(length=256)),
        sa.Column('release_state', sa.Enum('CURRENT', 'MISSING', 'REPLACED', 'REMOVED', 'CONFLICT', 'DUPLICATE', name='releasestate', native_enum=False), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('became_current_at', sa.DateTime(timezone=True)),
        sa.Column('replaced_at', sa.DateTime(timezone=True)),
        sa.Column('removed_at', sa.DateTime(timezone=True)),
        sa.Column('original_custom_format_score', sa.Integer()),
        sa.Column('current_custom_format_score', sa.Integer()),
        sa.Column('parser_version', sa.String(length=64)),
        sa.Column('parse_snapshot', JSON_TYPE, nullable=False),
        sa.Column('selection_snapshot', JSON_TYPE),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['quality_definition_id'], ['quality_definitions.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_movie_releases_movie_state', 'movie_releases', ['movie_id', 'release_state'], unique=False)

    op.create_table('movie_tags',
        sa.Column('movie_id', sa.Uuid(), nullable=False),
        sa.Column('tag_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('movie_id', 'tag_id', name='uq_movie_tag'),
        sa.PrimaryKeyConstraint('movie_id', 'tag_id'),
    )

    op.create_table('quality_profiles',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('minimum_quality_definition_id', sa.Uuid()),
        sa.Column('settings', JSON_TYPE, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['minimum_quality_definition_id'], ['quality_definitions.id'], ondelete='SET NULL'),
    )

    op.create_table('remote_path_mappings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('integration_type', sa.Enum('QBITTORRENT', 'PLEX', 'PROWLARR', 'TMDB', 'TVDB', name='integrationtype', native_enum=False), nullable=False),
        sa.Column('integration_id', sa.Uuid()),
        sa.Column('remote_prefix', sa.Text(), nullable=False),
        sa.Column('local_prefix', sa.Text(), nullable=False),
        sa.Column('storage_root_id', sa.Uuid()),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['storage_root_id'], ['storage_roots.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_remote_path_mappings_integration', 'remote_path_mappings', ['integration_type', 'integration_id'], unique=False)

    op.create_table('seasons',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('show_id', sa.Uuid(), nullable=False),
        sa.Column('season_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=512)),
        sa.Column('monitored', sa.Boolean(), nullable=False),
        sa.Column('metadata', JSON_TYPE, nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('show_id', 'season_number', name='uq_seasons_show_number'),
        sa.ForeignKeyConstraint(['show_id'], ['shows.id'], ondelete='CASCADE'),
    )

    op.create_table('torrent_client_observations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('torrent_id', sa.Uuid(), nullable=False),
        sa.Column('download_client_id', sa.Uuid(), nullable=False),
        sa.Column('reported_save_path', sa.Text()),
        sa.Column('resolved_save_path', sa.Text()),
        sa.Column('state', sa.String(length=128)),
        sa.Column('progress', sa.Numeric(precision=6, scale=5)),
        sa.Column('category', sa.String(length=256)),
        sa.Column('tags', JSON_TYPE, nullable=False),
        sa.Column('is_present', sa.Boolean(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('removed_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['download_client_id'], ['download_clients.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['torrent_id'], ['torrents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_torrent_observations_client_present', 'torrent_client_observations', ['download_client_id', 'is_present'], unique=False)

    op.create_table('episodes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('show_id', sa.Uuid(), nullable=False),
        sa.Column('season_id', sa.Uuid(), nullable=False),
        sa.Column('season_number', sa.Integer(), nullable=False),
        sa.Column('episode_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=512)),
        sa.Column('air_date', sa.Date()),
        sa.Column('tmdb_id', sa.Integer()),
        sa.Column('tvdb_id', sa.Integer()),
        sa.Column('monitored', sa.Boolean(), nullable=False),
        sa.Column('presence_state', sa.Enum('PRESENT', 'MISSING', 'UNMATCHED', 'CONFLICT', name='presencestate', native_enum=False), nullable=False),
        sa.Column('metadata', JSON_TYPE, nullable=False),
        sa.UniqueConstraint('show_id', 'season_number', 'episode_number', name='uq_episodes_show_season_number'),
        sa.ForeignKeyConstraint(['show_id'], ['shows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['season_id'], ['seasons.id'], ondelete='CASCADE'),
    )

    op.create_table('media_profile_overrides',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('media_type', sa.Enum('MOVIES', 'SHOWS', name='mediatype', native_enum=False), nullable=False),
        sa.Column('movie_id', sa.Uuid()),
        sa.Column('show_id', sa.Uuid()),
        sa.Column('quality_profile_id', sa.Uuid()),
        sa.Column('override_definition', JSON_TYPE, nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['quality_profile_id'], ['quality_profiles.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['show_id'], ['shows.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_media_profile_overrides_movie', 'media_profile_overrides', ['movie_id'], unique=False)
    op.create_index('ix_media_profile_overrides_show', 'media_profile_overrides', ['show_id'], unique=False)

    op.create_table('movie_release_torrents',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('movie_release_id', sa.Uuid(), nullable=False),
        sa.Column('torrent_id', sa.Uuid(), nullable=False),
        sa.Column('association_type', sa.Enum('INCOMING', 'ATTACHED', 'HISTORICAL', name='associationtype', native_enum=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['torrent_id'], ['torrents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['movie_release_id'], ['movie_releases.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('movie_release_id', 'torrent_id', name='uq_movie_release_torrent'),
    )

    op.create_table('profile_custom_format_scores',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('profile_id', sa.Uuid(), nullable=False),
        sa.Column('custom_format_id', sa.Uuid(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.UniqueConstraint('profile_id', 'custom_format_id', name='uq_profile_custom_format'),
        sa.ForeignKeyConstraint(['custom_format_id'], ['custom_formats.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['profile_id'], ['quality_profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('show_releases',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('show_id', sa.Uuid(), nullable=False),
        sa.Column('season_id', sa.Uuid()),
        sa.Column('raw_release_name', sa.Text(), nullable=False),
        sa.Column('release_scope', sa.Enum('EPISODE', 'MULTI_EPISODE', 'SEASON_PACK', 'OTHER', name='releasescope', native_enum=False), nullable=False),
        sa.Column('quality_definition_id', sa.Uuid()),
        sa.Column('release_group', sa.String(length=256)),
        sa.Column('release_state', sa.Enum('CURRENT', 'MISSING', 'REPLACED', 'REMOVED', 'CONFLICT', 'DUPLICATE', name='releasestate', native_enum=False), nullable=False),
        sa.Column('parse_snapshot', JSON_TYPE, nullable=False),
        sa.Column('original_custom_format_score', sa.Integer()),
        sa.Column('current_custom_format_score', sa.Integer()),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['season_id'], ['seasons.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['quality_definition_id'], ['quality_definitions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['show_id'], ['shows.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_show_releases_show_state', 'show_releases', ['show_id', 'release_state'], unique=False)

    op.create_table('media_directories',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('storage_root_id', sa.Uuid(), nullable=False),
        sa.Column('reported_path', sa.Text()),
        sa.Column('resolved_path', sa.Text(), nullable=False),
        sa.Column('path_mapping_id', sa.Uuid()),
        sa.Column('movie_release_id', sa.Uuid()),
        sa.Column('show_release_id', sa.Uuid()),
        sa.Column('exists', sa.Boolean(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_exists_check_at', sa.DateTime(timezone=True)),
        sa.Column('missing_since', sa.DateTime(timezone=True)),
        sa.Column('missing_check_count', sa.Integer(), nullable=False),
        sa.Column('source_type', sa.Enum('FILESYSTEM', 'TORRENT_NAME', 'DIRECTORY_NAME', 'FILENAME', 'PROWLARR_RESULT', name='sourcetype', native_enum=False), nullable=False),
        sa.Column('source_integration_id', sa.Uuid()),
        sa.ForeignKeyConstraint(['show_release_id'], ['show_releases.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['path_mapping_id'], ['remote_path_mappings.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['movie_release_id'], ['movie_releases.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['storage_root_id'], ['storage_roots.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_media_directories_resolved_path', 'media_directories', ['resolved_path'], unique=False)

    op.create_table('show_release_torrents',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('show_release_id', sa.Uuid(), nullable=False),
        sa.Column('torrent_id', sa.Uuid(), nullable=False),
        sa.Column('association_type', sa.Enum('INCOMING', 'ATTACHED', 'HISTORICAL', name='associationtype', native_enum=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['torrent_id'], ['torrents.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('show_release_id', 'torrent_id', name='uq_show_release_torrent'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['show_release_id'], ['show_releases.id'], ondelete='CASCADE'),
    )

    op.create_table('media_files',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('media_directory_id', sa.Uuid(), nullable=False),
        sa.Column('relative_path', sa.Text(), nullable=False),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('media_role', sa.Enum('MOVIE_VIDEO', 'EPISODE_VIDEO', 'MULTI_EPISODE_VIDEO', 'DVD_STRUCTURE', 'BLURAY_STRUCTURE', 'OTHER_MEDIA', name='mediarole', native_enum=False), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('exists', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('media_directory_id', 'relative_path', name='uq_media_files_directory_relative'),
        sa.ForeignKeyConstraint(['media_directory_id'], ['media_directories.id'], ondelete='CASCADE'),
    )

    op.create_table('episode_media_maps',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('episode_id', sa.Uuid(), nullable=False),
        sa.Column('media_file_id', sa.Uuid(), nullable=False),
        sa.Column('show_release_id', sa.Uuid()),
        sa.Column('match_method', sa.Enum('PARSER', 'PLEX', 'MANUAL', 'COMBINED', name='matchmethod', native_enum=False), nullable=False),
        sa.Column('confidence', sa.Numeric(precision=5, scale=4)),
        sa.Column('manual_override', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['show_release_id'], ['show_releases.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('episode_id', 'media_file_id', name='uq_episode_media_map'),
        sa.ForeignKeyConstraint(['media_file_id'], ['media_files.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('plex_observations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('media_type', sa.Enum('MOVIES', 'SHOWS', name='mediatype', native_enum=False), nullable=False),
        sa.Column('movie_id', sa.Uuid()),
        sa.Column('show_id', sa.Uuid()),
        sa.Column('episode_id', sa.Uuid()),
        sa.Column('movie_release_id', sa.Uuid()),
        sa.Column('media_file_id', sa.Uuid()),
        sa.Column('plex_rating_key', sa.String(length=256)),
        sa.Column('plex_title', sa.String(length=512)),
        sa.Column('plex_year', sa.Integer()),
        sa.Column('plex_edition', sa.String(length=128)),
        sa.Column('plex_reported_path', sa.Text()),
        sa.Column('resolved_path', sa.Text()),
        sa.Column('match_state', sa.Enum('MATCHED', 'NOT_FOUND', 'PENDING', 'CONFLICT', 'MULTIPLE_VERSIONS', 'UNAVAILABLE', name='plexmatchstate', native_enum=False), nullable=False),
        sa.Column('match_method', sa.Enum('EXACT_PATH', 'TITLE_YEAR', 'MANUAL', name='plexmatchmethod', native_enum=False)),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['media_file_id'], ['media_files.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('movie_release_id', name='uq_plex_observation_movie_release'),
        sa.ForeignKeyConstraint(['show_id'], ['shows.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['movie_release_id'], ['movie_releases.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_plex_observations_path', 'plex_observations', ['resolved_path'], unique=False)



def downgrade() -> None:
    op.drop_table('plex_observations')
    op.drop_table('episode_media_maps')
    op.drop_table('media_files')
    op.drop_table('show_release_torrents')
    op.drop_table('media_directories')
    op.drop_table('show_releases')
    op.drop_table('profile_custom_format_scores')
    op.drop_table('movie_release_torrents')
    op.drop_table('media_profile_overrides')
    op.drop_table('episodes')
    op.drop_table('torrent_client_observations')
    op.drop_table('seasons')
    op.drop_table('remote_path_mappings')
    op.drop_table('quality_profiles')
    op.drop_table('movie_tags')
    op.drop_table('movie_releases')
    op.drop_table('auth_sessions')
    op.drop_table('torrents')
    op.drop_table('tmdb_configurations')
    op.drop_table('tags')
    op.drop_table('storage_roots')
    op.drop_table('shows')
    op.drop_table('schedules')
    op.drop_table('quality_definitions')
    op.drop_table('problems')
    op.drop_table('plex_configurations')
    op.drop_table('parse_evidence')
    op.drop_table('movies')
    op.drop_table('jobs')
    op.drop_table('indexers')
    op.drop_table('events')
    op.drop_table('download_clients')
    op.drop_table('custom_formats')
    op.drop_table('admin_users')
