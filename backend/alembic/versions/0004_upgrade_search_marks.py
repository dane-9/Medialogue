"""When each title was last searched for upgrades.

Revision ID: 0004
Revises: 0003

Most titles have no upgrade candidate to remember this on, and the search goes
longest-unsearched first. 0001 creates the schema from the current models, so a
fresh database already has this table; IF NOT EXISTS covers both.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS upgrade_search_marks ("
        "target_key VARCHAR(160) NOT NULL, "
        "searched_at DATETIME NOT NULL, "
        "PRIMARY KEY (target_key))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS upgrade_search_marks")
