"""Completed downloads whose files Medialogue cannot find.

Two rules keep this Problem worth reading:

- It is raised only once a path has stayed missing for several qBittorrent
  passes in a row. A move, recheck or slow mount makes a path vanish briefly,
  and that is not something to act on.
- It is raised once per download client, listing every affected torrent. A
  wrong path mapping breaks every torrent behind it at once, and one Problem
  per torrent buried the single cause under copies of itself.

The per-torrent state lives in the torrent's metadata; the Problem holds the
list, so it can be read without joining anything.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Problem, ProblemStatus, Torrent, TorrentClientObservation

REASON = "TORRENT_PATH_NOT_FOUND"
ENTITY_TYPE = "download_client"
#: Consecutive full passes a path must stay missing before it is a Problem.
PASSES_BEFORE_PROBLEM = 3
_PASSES_KEY = "path_missing_passes"


def path_check_pending(torrent: Torrent) -> bool:
    """A missing path is being watched, so the torrent must not take the fast path."""

    return bool((torrent.metadata_json or {}).get(_PASSES_KEY))


async def report_missing_path(
    db: AsyncSession,
    torrent: Torrent,
    *,
    download_client_id: UUID,
    message: str,
    details: dict[str, Any] | None = None,
) -> bool:
    """Count one more pass with the path missing. True once it is a Problem."""

    metadata = dict(torrent.metadata_json or {})
    passes = int(metadata.get(_PASSES_KEY) or 0) + 1
    metadata[_PASSES_KEY] = passes
    torrent.metadata_json = metadata
    if passes < PASSES_BEFORE_PROBLEM:
        return False

    entry = {
        "torrent_id": str(torrent.id),
        "name": torrent.name,
        "message": message,
        "mapping_id": metadata.get("mapping_id"),
        **{key: value for key, value in (details or {}).items() if key in {"resolved_path", "path", "error", "root"}},
    }
    observation = await db.scalar(
        select(TorrentClientObservation).where(
            TorrentClientObservation.torrent_id == torrent.id,
            TorrentClientObservation.download_client_id == download_client_id,
        )
    )
    if observation is not None:
        entry["reported_path"] = observation.reported_save_path
        entry["resolved_path"] = entry.get("resolved_path") or observation.resolved_save_path
    group = await _group(db, download_client_id)
    entries = [item for item in _entries(group) if item.get("torrent_id") != entry["torrent_id"]]
    await _save_group(db, download_client_id, [*entries, entry])
    return True


async def clear_missing_path(db: AsyncSession, torrent: Torrent) -> None:
    """The path was found, or no longer matters: forget the misses."""

    metadata = torrent.metadata_json or {}
    reported = int(metadata.get(_PASSES_KEY) or 0) >= PASSES_BEFORE_PROBLEM
    if _PASSES_KEY in metadata:
        torrent.metadata_json = {key: value for key, value in metadata.items() if key != _PASSES_KEY}
    if not reported:
        return
    torrent_key = str(torrent.id)
    groups = (await db.scalars(
        select(Problem).where(
            Problem.reason == REASON,
            Problem.entity_type == ENTITY_TYPE,
            Problem.status.in_([ProblemStatus.OPEN, ProblemStatus.DISMISSED]),
        )
    )).all()
    for group in groups:
        entries = _entries(group)
        if any(item.get("torrent_id") == torrent_key for item in entries) and group.entity_id is not None:
            await _save_group(db, group.entity_id, [item for item in entries if item.get("torrent_id") != torrent_key])


def _entries(problem: Problem | None) -> list[dict[str, Any]]:
    if problem is None:
        return []
    value = (problem.details or {}).get("torrents")
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


async def _group(db: AsyncSession, download_client_id: UUID) -> Problem | None:
    return await db.scalar(
        select(Problem).where(
            Problem.reason == REASON,
            Problem.entity_type == ENTITY_TYPE,
            Problem.entity_id == download_client_id,
            Problem.status.in_([ProblemStatus.OPEN, ProblemStatus.DISMISSED]),
        ).order_by(Problem.created_at.desc()).limit(1)
    )


async def _save_group(db: AsyncSession, download_client_id: UUID, entries: list[dict[str, Any]]) -> None:
    from app.services.integration_state import get_configured_download_client
    from app.services.reconciliation import open_problem, resolve_problem

    if not entries:
        await resolve_problem(db, REASON, ENTITY_TYPE, download_client_id)
        return
    client = await get_configured_download_client(db, download_client_id)
    client_name = client.name if client is not None else "qBittorrent"
    count = len(entries)
    mapping_ids = sorted({str(item["mapping_id"]) for item in entries if item.get("mapping_id")})
    await open_problem(
        db,
        reason=REASON,
        entity_type=ENTITY_TYPE,
        entity_id=download_client_id,
        message=(
            f"{count} completed download{'s' if count != 1 else ''} in {client_name} "
            f"cannot be found on disk."
        ),
        details={
            "client_id": str(download_client_id),
            "client_name": client_name,
            "count": count,
            "mapping_ids": mapping_ids,
            "torrents": sorted(entries, key=lambda item: str(item.get("name") or "").casefold()),
        },
    )
