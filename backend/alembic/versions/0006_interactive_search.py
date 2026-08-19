"""Add indexer health/configuration state and persistent interactive search results."""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_interactive_search"
down_revision = "0005_foundation_fixes"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column("indexers", sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="15"))
        op.add_column("indexers", sa.Column("health", sa.String(length=32), nullable=False, server_default="unknown"))
        op.add_column("indexers", sa.Column("last_checked_at", sa.DateTime(timezone=True)))
        op.add_column("indexers", sa.Column("last_success_at", sa.DateTime(timezone=True)))
        op.add_column("indexers", sa.Column("latency_ms", sa.Integer()))
        op.add_column("indexers", sa.Column("last_error", sa.Text()))
        op.add_column("indexers", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        _create_search_results()
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("indexers")}
    additions = (
        ("timeout_seconds", sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="15")),
        ("health", sa.Column("health", sa.String(length=32), nullable=False, server_default="unknown")),
        ("last_checked_at", sa.Column("last_checked_at", sa.DateTime(timezone=True))),
        ("last_success_at", sa.Column("last_success_at", sa.DateTime(timezone=True))),
        ("latency_ms", sa.Column("latency_ms", sa.Integer())),
        ("last_error", sa.Column("last_error", sa.Text())),
        ("revision", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("indexers", column)

    if "search_results" not in set(inspector.get_table_names()):
        _create_search_results()


def _create_search_results() -> None:
    op.create_table(
        "search_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("indexer_id", sa.Uuid()),
        sa.Column("indexer_name", sa.String(length=256), nullable=False),
        sa.Column("media_type", sa.Enum("MOVIES", "SHOWS", name="mediatype", native_enum=False), nullable=False),
        sa.Column("target_entity_type", sa.String(length=64), nullable=False),
        sa.Column("target_entity_id", sa.Uuid(), nullable=False),
        sa.Column("guid", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("download_url", sa.Text()),
        sa.Column("size", sa.BigInteger()),
        sa.Column("seeders", sa.Integer()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("parser_version", sa.String(length=64)),
        sa.Column("parse_snapshot", JSON_TYPE, nullable=False),
        sa.Column("quality", sa.String(length=256)),
        sa.Column("edition", sa.String(length=128)),
        sa.Column("release_group", sa.String(length=256)),
        sa.Column("custom_format_score", sa.Integer()),
        sa.Column("custom_format_snapshot", JSON_TYPE, nullable=False),
        sa.Column("warnings", JSON_TYPE, nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True)),
        sa.Column("selected_download_client_id", sa.Uuid()),
        sa.Column("selection_snapshot", JSON_TYPE),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["indexer_id"], ["indexers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["selected_download_client_id"], ["download_clients.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "indexer_id", "guid", name="uq_search_result_job_indexer_guid"),
    )
    op.create_index("ix_search_results_job_created", "search_results", ["job_id", "created_at"], unique=False)
    op.create_index("ix_search_results_expiry", "search_results", ["expires_at"], unique=False)


def downgrade() -> None:
    # Development migrations are forward-only. Search-result history may
    # contain selected release evidence and should not be dropped casually.
    return
