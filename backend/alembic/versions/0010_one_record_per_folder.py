"""One record per media folder, and one qBittorrent observation per torrent and client.

Revision ID: 0010
Revises: 0009

Two workers (the folder watcher and qBittorrent completion, or a scan) could
each record a new folder before the other had saved it, which showed the same
movie twice: two releases for one folder, or two New Media entries. Duplicate
folder records are merged here, then a unique index makes the database refuse
a second record of a folder. The same is done for qBittorrent observations.

0001 creates the schema from the current models, so a fresh database already
has these indexes; IF NOT EXISTS covers both.
"""

from datetime import datetime, timezone

from alembic import context, op
from sqlalchemy import text

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_PRESENT = ("CURRENT", "DUPLICATE")


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def _rows(bind, sql: str, **params):
    return bind.execute(text(sql), params).mappings().all()


def _keeper(bind, directories):
    """The record that keeps the folder: a present release first, then any release, one qBittorrent tracks, New Media, the oldest."""

    def rank(row):
        state = None
        if row["movie_release_id"]:
            state = bind.execute(text("SELECT release_state FROM movie_releases WHERE id = :id"), {"id": row["movie_release_id"]}).scalar()
        elif row["show_release_id"]:
            state = bind.execute(text("SELECT release_state FROM show_releases WHERE id = :id"), {"id": row["show_release_id"]}).scalar()
        has_release = bool(row["movie_release_id"] or row["show_release_id"])
        tracked = False
        if row["movie_release_id"]:
            tracked = bool(bind.execute(text("SELECT 1 FROM movie_release_torrents WHERE movie_release_id = :id"),
                                        {"id": row["movie_release_id"]}).scalar())
        elif row["show_release_id"]:
            tracked = bool(bind.execute(text("SELECT 1 FROM show_release_torrents WHERE show_release_id = :id"),
                                        {"id": row["show_release_id"]}).scalar())
        has_discovery = bool(bind.execute(
            text("SELECT 1 FROM media_discoveries WHERE media_directory_id = :id"), {"id": row["id"]}).scalar())
        return (state not in _PRESENT, not has_release, not tracked, not has_discovery, str(row["first_seen_at"]))

    return min(directories, key=rank)


def _merge_files(bind, keeper_id: str, loser_id: str) -> None:
    keeper_files = {row["relative_path"]: row["id"] for row in _rows(
        bind, "SELECT id, relative_path FROM media_files WHERE media_directory_id = :id", id=keeper_id)}
    for row in _rows(bind, "SELECT id, relative_path FROM media_files WHERE media_directory_id = :id", id=loser_id):
        twin = keeper_files.get(row["relative_path"])
        if twin is None:
            bind.execute(text("UPDATE media_files SET media_directory_id = :keeper WHERE id = :id"), {"keeper": keeper_id, "id": row["id"]})
            continue
        # The same file under both records: its episode mappings move to the kept file.
        bind.execute(text(
            "UPDATE episode_media_maps SET media_file_id = :twin WHERE media_file_id = :id "
            "AND episode_id NOT IN (SELECT episode_id FROM episode_media_maps WHERE media_file_id = :twin)"
        ), {"twin": twin, "id": row["id"]})
        bind.execute(text("DELETE FROM media_files WHERE id = :id"), {"id": row["id"]})


def _merge_release(bind, kind: str, keeper_release: str, loser_release: str) -> None:
    links = f"{kind}_release_torrents"
    column = f"{kind}_release_id"
    bind.execute(text(
        f"UPDATE {links} SET {column} = :keeper WHERE {column} = :loser "
        f"AND torrent_id NOT IN (SELECT torrent_id FROM {links} WHERE {column} = :keeper)"
    ), {"keeper": keeper_release, "loser": loser_release})
    bind.execute(text(f"DELETE FROM {links} WHERE {column} = :loser"), {"loser": loser_release})
    if kind == "show":
        bind.execute(text("UPDATE episode_media_maps SET show_release_id = :keeper WHERE show_release_id = :loser"),
                     {"keeper": keeper_release, "loser": loser_release})
    bind.execute(text(
        f"UPDATE {kind}_releases SET release_state = 'REMOVED', removed_at = :now WHERE id = :loser"
    ), {"now": _now(), "loser": loser_release})
    # The kept release no longer has a twin; a later check reopens a real duplicate.
    bind.execute(text(
        f"UPDATE {kind}_releases SET release_state = 'CURRENT' WHERE id = :keeper AND release_state = 'DUPLICATE'"
    ), {"keeper": keeper_release})


def _merge_directories(bind) -> None:
    groups = bind.execute(text(
        "SELECT resolved_path FROM media_directories GROUP BY resolved_path HAVING COUNT(*) > 1"
    )).scalars().all()
    for path in groups:
        directories = _rows(bind, (
            "SELECT id, movie_release_id, show_release_id, first_seen_at FROM media_directories "
            "WHERE resolved_path = :path ORDER BY first_seen_at, id"
        ), path=path)
        keeper = dict(_keeper(bind, directories))
        for loser in directories:
            if loser["id"] == keeper["id"]:
                continue
            _merge_files(bind, keeper["id"], loser["id"])
            if bind.execute(text("SELECT 1 FROM media_discoveries WHERE media_directory_id = :id"), {"id": keeper["id"]}).scalar():
                bind.execute(text("DELETE FROM media_discoveries WHERE media_directory_id = :id"), {"id": loser["id"]})
            else:
                bind.execute(text("UPDATE media_discoveries SET media_directory_id = :keeper WHERE media_directory_id = :id"),
                             {"keeper": keeper["id"], "id": loser["id"]})
            for kind in ("movie", "show"):
                column = f"{kind}_release_id"
                if not loser[column] or loser[column] == keeper[column]:
                    continue
                if keeper[column]:
                    owner = f"{kind}_id"
                    owners = bind.execute(text(
                        f"SELECT COUNT(DISTINCT {owner}) FROM {kind}_releases WHERE id IN (:keeper, :loser)"
                    ), {"keeper": keeper[column], "loser": loser[column]}).scalar()
                    # Releases of two different titles are left alone; only the record goes.
                    if owners == 1:
                        _merge_release(bind, kind, keeper[column], loser[column])
                else:
                    bind.execute(text(f"UPDATE media_directories SET {column} = :release WHERE id = :id"),
                                 {"release": loser[column], "id": keeper["id"]})
                    keeper[column] = loser[column]
            bind.execute(text("DELETE FROM media_directories WHERE id = :id"), {"id": loser["id"]})


def _merge_observations(bind) -> None:
    groups = _rows(bind, (
        "SELECT torrent_id, download_client_id FROM torrent_client_observations "
        "GROUP BY torrent_id, download_client_id HAVING COUNT(*) > 1"
    ))
    for group in groups:
        rows = bind.execute(text(
            "SELECT id FROM torrent_client_observations WHERE torrent_id = :torrent AND download_client_id = :client "
            "ORDER BY last_seen_at DESC, id"
        ), {"torrent": group["torrent_id"], "client": group["download_client_id"]}).scalars().all()
        for row_id in rows[1:]:
            bind.execute(text("DELETE FROM torrent_client_observations WHERE id = :id"), {"id": row_id})


def upgrade() -> None:
    # Offline SQL generation has no rows to merge; it only emits the indexes.
    if not context.is_offline_mode():
        bind = op.get_bind()
        _merge_directories(bind)
        _merge_observations(bind)
    op.execute("DROP INDEX IF EXISTS ix_media_directories_resolved_path")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_media_directories_resolved_path ON media_directories (resolved_path)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_torrent_observations_torrent_client "
        "ON torrent_client_observations (torrent_id, download_client_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_torrent_observations_torrent_client")
    op.execute("DROP INDEX IF EXISTS uq_media_directories_resolved_path")
    op.execute("CREATE INDEX IF NOT EXISTS ix_media_directories_resolved_path ON media_directories (resolved_path)")
