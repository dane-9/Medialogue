"""Persisted cross-seed payload groups.

Torrents that share one physical payload (the same release association, the
same qBittorrent content path, or hard-linked files) belong to one group. The
group key is stored on ``Torrent.metadata_json["payload_group"]`` so the
Downloads list can page, sort and count by payload in SQL instead of working it
out from the filesystem on every request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    AssociationType,
    MovieReleaseTorrent,
    ShowReleaseTorrent,
    Torrent,
    TorrentClientObservation,
)

logger = logging.getLogger(__name__)

GROUP_KEY = "payload_group"
IDENTITY_KEY = "payload_identity"

#: Hard-link identities are computed from disk; spread the first fill of a big
#: client over several runs instead of stalling one poll.
_IDENTITY_BUDGET_PER_RUN = 200
#: Groups are rebuilt at least this often even when nothing obviously changed.
_REFRESH_SECONDS = 600
_last_refresh = 0.0


def group_key_for(torrent_id: UUID) -> str:
    """The key of a group whose smallest member is ``torrent_id``.

    Uses the 32-character hex form so it equals SQLite's stored UUID text,
    which lets SQL fall back to ``'payload:' || torrents.id`` for ungrouped rows.
    """

    return f"payload:{torrent_id.hex}"


def refresh_due(*, structure_changed: bool) -> bool:
    return structure_changed or time.monotonic() - _last_refresh >= _REFRESH_SECONDS


def _path_key(path: str) -> str:
    return str(Path(path).resolve(strict=False)).casefold()


def _compute_identities(work: list[tuple[UUID, Torrent, UUID, str]]) -> dict[UUID, tuple[str, list[list[int]]]]:
    """Filesystem part, run in a worker thread."""

    from app.services.upgrade_payloads import _archived_file_signatures, _payload_file_identities

    result: dict[UUID, tuple[str, list[list[int]]]] = {}
    for torrent_id, torrent, client_id, resolved in work:
        try:
            signatures = _archived_file_signatures(torrent, client_id)
            identities = (
                _payload_file_identities([Path(resolved)], file_signatures=signatures) if signatures else set()
            )
        except OSError:
            identities = set()
        result[torrent_id] = (_path_key(resolved), sorted([list(item) for item in identities]))
    return result


async def refresh_payload_groups(db: AsyncSession) -> int:
    """Recompute and persist payload groups for every present torrent.

    Returns how many torrents changed group. The caller commits.
    """

    global _last_refresh
    _last_refresh = time.monotonic()

    rows = (
        await db.execute(
            select(TorrentClientObservation, Torrent)
            .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
            .where(TorrentClientObservation.is_present.is_(True))
        )
    ).all()
    if not rows:
        return 0
    torrents: dict[UUID, Torrent] = {torrent.id: torrent for _observation, torrent in rows}
    parent = {torrent_id: torrent_id for torrent_id in torrents}

    def find(value: UUID) -> UUID:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(first: UUID, second: UUID) -> None:
        left, right = find(first), find(second)
        if left != right:
            parent[max(left, right, key=lambda item: item.hex)] = min(left, right, key=lambda item: item.hex)

    # 1. Same release (a cross-seed Medialogue already linked).
    ids = list(torrents)
    release_owner: dict[UUID, UUID] = {}
    for model, column in (
        (MovieReleaseTorrent, MovieReleaseTorrent.movie_release_id),
        (ShowReleaseTorrent, ShowReleaseTorrent.show_release_id),
    ):
        for index in range(0, len(ids), 500):
            chunk = ids[index : index + 500]
            for torrent_id, release_id in (
                await db.execute(
                    select(model.torrent_id, column).where(
                        model.torrent_id.in_(chunk),
                        model.association_type != AssociationType.HISTORICAL,
                    )
                )
            ).all():
                if release_id in release_owner:
                    union(torrent_id, release_owner[release_id])
                else:
                    release_owner[release_id] = torrent_id

    # 2. Same exact content path, and 3. hard-linked payload files. Only a real
    # qBittorrent content path is specific enough; a save-path fallback can be
    # a whole library folder.
    path_owner: dict[str, UUID] = {}
    stale_identity: list[tuple[UUID, Torrent, UUID, str]] = []
    identities: dict[UUID, set[tuple[int, int]]] = {}
    for observation, torrent in rows:
        metadata = torrent.metadata_json or {}
        resolved = str(observation.resolved_save_path or "").strip()
        if not resolved or not str(metadata.get("content_path") or "").strip():
            continue
        key = _path_key(resolved)
        if key in path_owner:
            union(torrent.id, path_owner[key])
        else:
            path_owner[key] = torrent.id
        cached = metadata.get(IDENTITY_KEY)
        if isinstance(cached, dict) and cached.get("path") == key:
            values = {tuple(item) for item in cached.get("inodes") or [] if isinstance(item, list) and len(item) == 2}
            if values:
                identities[torrent.id] = values
        elif len(stale_identity) < _IDENTITY_BUDGET_PER_RUN:
            stale_identity.append((torrent.id, torrent, observation.download_client_id, resolved))

    if stale_identity:
        computed = await asyncio.to_thread(_compute_identities, stale_identity)
        for torrent_id, (key, inodes) in computed.items():
            torrent = torrents[torrent_id]
            torrent.metadata_json = {**(torrent.metadata_json or {}), IDENTITY_KEY: {"path": key, "inodes": inodes}}
            if inodes:
                identities[torrent_id] = {tuple(item) for item in inodes}

    owner_by_inode: dict[tuple[int, int], UUID] = {}
    for torrent_id, values in identities.items():
        for inode in values:
            if inode in owner_by_inode:
                union(torrent_id, owner_by_inode[inode])
            else:
                owner_by_inode[inode] = torrent_id

    changed = 0
    for torrent_id, torrent in torrents.items():
        key = group_key_for(find(torrent_id))
        metadata: dict[str, Any] = torrent.metadata_json or {}
        if metadata.get(GROUP_KEY) != key:
            torrent.metadata_json = {**metadata, GROUP_KEY: key}
            changed += 1
    return changed
