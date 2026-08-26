"""Compatibility marker for the removed Plex metadata-provider experiment.

Revision ID: 0003
Revises: 0002
"""

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Some development databases ran the original 0003 before the feature was
    # retired. 0004 removes its columns; fresh databases have nothing to add.
    pass


def downgrade() -> None:
    pass
