"""Remove upgrade candidates that are a different movie or show.

Revision ID: 0005
Revises: 0004

Upgrade searches used to accept whatever a tracker's text search returned, so
"Ray 2004" could be offered "50 First Dates 2004 ... Blu-ray". Searches now check
the title; this clears what they stored before. Only rows that have not been
started are touched. The titles losing a row are marked unsearched so they are
searched again first. A release a tracker identified by TMDB id under another
name is cleared too, and comes back on that search.
"""

import json

import sqlalchemy as sa
from alembic import context, op

from app.core.identity import identity_titles_equivalent
from app.parser import parse_release

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _is_same_title(release: str, title: str, year: int | None) -> bool:
    identity = parse_release(release).identity
    if year and identity.year and identity.year != year:
        return False
    return any(identity_titles_equivalent(candidate, title) for candidate in (identity.title_candidate, *identity.alternate_titles))


def upgrade() -> None:
    # Which rows are wrong depends on the data, so offline SQL has nothing to say.
    if context.is_offline_mode():
        return

    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT c.id, c.target_key, c.title, c.search_results, m.title AS movie_title, m.year AS movie_year, s.title AS show_title "
        "FROM upgrade_candidates c "
        "LEFT JOIN movies m ON m.id = c.movie_id "
        "LEFT JOIN seasons n ON n.id = c.season_id "
        "LEFT JOIN shows s ON s.id = n.show_id "
        "WHERE c.target_key NOT LIKE 'replace:%' AND (c.execution_state IS NULL OR c.execution_state = 'failed')"
    )).mappings().all()

    for row in rows:
        title = row["movie_title"] or row["show_title"]
        if not title:
            continue
        year = row["movie_year"]
        if not _is_same_title(row["title"], title, year):
            connection.execute(sa.text("DELETE FROM upgrade_candidates WHERE id = :id"), {"id": row["id"]})
            connection.execute(sa.text("DELETE FROM upgrade_search_marks WHERE target_key = :key"), {"key": row["target_key"]})
            continue
        # The chosen release is right, but "View all releases" could still
        # offer a wrong one.
        results = json.loads(row["search_results"]) if isinstance(row["search_results"], str) else (row["search_results"] or [])
        kept = [entry for entry in results if _is_same_title(str(entry.get("title") or ""), title, year)]
        if len(kept) != len(results):
            connection.execute(
                sa.text("UPDATE upgrade_candidates SET search_results = :results WHERE id = :id"),
                {"results": json.dumps(kept), "id": row["id"]},
            )


def downgrade() -> None:
    # The removed rows were wrong, and a search rebuilds the rest.
    pass
