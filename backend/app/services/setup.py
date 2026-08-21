from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.auth import AdminUser
from app.models.domain import DownloadClient, Indexer, PlexConfiguration, StorageRoot, TMDBConfiguration
from app.schemas.setup import SetupStatusResponse, SetupStep


_STATE_FILE = "setup-state.json"


def _state_path() -> Path:
    return Path(get_settings().config_dir) / _STATE_FILE


def read_setup_complete() -> bool:
    path = _state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    return bool(payload.get("wizard_complete"))


def write_setup_complete(complete: bool) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "wizard_complete": bool(complete)}
    # Atomic replacement keeps a partially written setup marker from trapping a
    # fresh install in an inconsistent state after an unexpected restart.
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


async def setup_status(db: AsyncSession, admin: AdminUser) -> SetupStatusResponse:
    tmdb = await db.scalar(select(TMDBConfiguration).limit(1))
    plex = await db.scalar(select(PlexConfiguration).limit(1))
    qbit_count = int(await db.scalar(select(func.count()).select_from(DownloadClient)) or 0)
    root_count = int(await db.scalar(select(func.count()).select_from(StorageRoot)) or 0)
    indexer_count = int(await db.scalar(select(func.count()).select_from(Indexer)) or 0)
    scanned_count = int(
        await db.scalar(select(func.count()).select_from(StorageRoot).where(StorageRoot.last_scan_at.is_not(None))) or 0
    )

    steps = [
        SetupStep(
            key="security",
            title="Change the default password",
            complete=not admin.is_default_password,
            optional=True,
            detail=(
                "Default password has been changed."
                if not admin.is_default_password
                else "Recommended before normal use. Medialogue will continue warning while admin/adminadmin is active."
            ),
            settings_tab="Security",
        ),
        SetupStep(
            key="metadata",
            title="Configure TMDB metadata",
            complete=bool(tmdb and tmdb.api_key and tmdb.enabled),
            optional=True,
            detail="TMDB is the primary identity source for newly discovered Movies and Shows.",
            settings_tab="Metadata",
        ),
        SetupStep(
            key="plex",
            title="Configure Plex",
            complete=bool(plex and plex.token and plex.enabled),
            optional=True,
            detail="Optional secondary verification. Plex remains read-only.",
            settings_tab="Plex",
        ),
        SetupStep(
            key="qbittorrent",
            title="Add qBittorrent client(s)",
            complete=qbit_count > 0,
            optional=True,
            detail=f"{qbit_count} client(s) configured. Clients are scoped to Movies or Shows.",
            settings_tab="qBittorrent",
        ),
        SetupStep(
            key="storage",
            title="Add storage roots",
            complete=root_count > 0,
            optional=True,
            detail=f"{root_count} explicit storage root(s) configured. Medialogue never scans outside them.",
            settings_tab="Storage Roots",
        ),
        SetupStep(
            key="indexers",
            title="Add Prowlarr-backed indexers",
            complete=indexer_count > 0,
            optional=True,
            detail=f"{indexer_count} indexer(s) configured. Indexers are added individually; Prowlarr configuration is not imported wholesale.",
            settings_tab="Indexers",
        ),
        SetupStep(
            key="schedules",
            title="Review polling schedules",
            complete=qbit_count > 0,
            optional=True,
            detail="qBittorrent polling is configured per client. Full storage-root scans remain manual unless a future explicit schedule is added.",
            settings_tab="Schedules",
        ),
        SetupStep(
            key="first_scan",
            title="Run the first library scan",
            complete=scanned_count > 0,
            optional=True,
            detail=(
                f"{scanned_count} root(s) have been scanned."
                if scanned_count
                else "Explicitly choose Scan now on each new storage root once. Setup never starts the initialization scan automatically."
            ),
            settings_tab="Storage Roots",
        ),
    ]
    complete = read_setup_complete()
    return SetupStatusResponse(wizard_complete=complete, wizard_required=not complete, steps=steps)
