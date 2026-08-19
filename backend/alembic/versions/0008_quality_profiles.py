"""Add revision and assignment constraints for Quality Profiles."""

from alembic import context, op
import sqlalchemy as sa

revision = "0008_quality_profiles"
down_revision = "0007_custom_formats"
branch_labels = None
depends_on = None


def _index_names(inspector, table: str) -> set[str]:
    return {item.get("name") for item in inspector.get_indexes(table) if item.get("name")}


def _unique_names(inspector, table: str) -> set[str]:
    return {item.get("name") for item in inspector.get_unique_constraints(table) if item.get("name")}


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column("quality_profiles", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        op.add_column("media_profile_overrides", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        op.create_index("uq_quality_profiles_name", "quality_profiles", ["name"], unique=True)
        op.create_index("uq_media_profile_override_movie", "media_profile_overrides", ["movie_id"], unique=True)
        op.create_index("uq_media_profile_override_show", "media_profile_overrides", ["show_id"], unique=True)
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    profile_columns = {item["name"] for item in inspector.get_columns("quality_profiles")}
    if "revision" not in profile_columns:
        op.add_column("quality_profiles", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))

    override_columns = {item["name"] for item in inspector.get_columns("media_profile_overrides")}
    if "revision" not in override_columns:
        op.add_column("media_profile_overrides", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))

    profile_unique_names = _unique_names(inspector, "quality_profiles") | _index_names(inspector, "quality_profiles")
    if "uq_quality_profiles_name" not in profile_unique_names:
        op.create_index("uq_quality_profiles_name", "quality_profiles", ["name"], unique=True)

    override_unique_names = _unique_names(inspector, "media_profile_overrides") | _index_names(inspector, "media_profile_overrides")
    if "uq_media_profile_override_movie" not in override_unique_names:
        op.create_index("uq_media_profile_override_movie", "media_profile_overrides", ["movie_id"], unique=True)
    if "uq_media_profile_override_show" not in override_unique_names:
        op.create_index("uq_media_profile_override_show", "media_profile_overrides", ["show_id"], unique=True)


def downgrade() -> None:
    return
