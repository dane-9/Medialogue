"""Foundation corrections: TMDB identity, CF enablement, and identity uniqueness.

This revision exists primarily for databases that had already reached the old
0004 development schema. Fresh databases already contain the same columns and
constraints in the explicit 0001 migration, so every operation is idempotent.
"""

from alembic import context, op
import sqlalchemy as sa

revision = "0005_foundation_fixes"
down_revision = "0004_reconciliation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        # Offline SQL is documentation only here; online mode inspects the
        # target DB so both old and fresh development histories are supported.
        op.execute("ALTER TABLE custom_formats ADD COLUMN IF NOT EXISTS enabled BOOLEAN DEFAULT TRUE NOT NULL")
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_movies_tmdb_id ON movies (tmdb_id)")
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_shows_tmdb_id ON shows (tmdb_id)")
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_plex_observation_movie_release ON plex_observations (movie_release_id)")
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "tmdb_configurations" not in tables:
        op.create_table(
            "tmdb_configurations",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("api_key", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("health", sa.String(length=32), nullable=False),
            sa.Column("last_checked_at", sa.DateTime(timezone=True)),
            sa.Column("last_success_at", sa.DateTime(timezone=True)),
            sa.Column("latency_ms", sa.Integer()),
            sa.Column("last_error", sa.Text()),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    custom_columns = {item["name"] for item in inspector.get_columns("custom_formats")}
    if "enabled" not in custom_columns:
        op.add_column(
            "custom_formats",
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )

    # Unique indexes are used for compatibility upgrades. Fresh 0001 databases
    # have equivalent named UNIQUE constraints; avoid creating duplicates.
    _ensure_unique_identity_index(inspector, "movies", "tmdb_id", "uq_movies_tmdb_id")
    _ensure_unique_identity_index(inspector, "shows", "tmdb_id", "uq_shows_tmdb_id")
    _ensure_unique_identity_index(
        inspector,
        "plex_observations",
        "movie_release_id",
        "uq_plex_observation_movie_release",
    )


def _ensure_unique_identity_index(inspector, table: str, column: str, name: str) -> None:
    unique_constraints = {item.get("name") for item in inspector.get_unique_constraints(table)}
    indexes = {item.get("name") for item in inspector.get_indexes(table)}
    if name in unique_constraints or name in indexes:
        return
    op.create_index(name, table, [column], unique=True)


def downgrade() -> None:
    # Forward-only compatibility revision. Fresh databases already receive
    # these schema elements from 0001, so removing them would make the schema
    # inconsistent with the target revision.
    return
