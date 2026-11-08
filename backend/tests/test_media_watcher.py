"""Live media folder watching: which folder a change belongs to, and what checking it does."""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_media_discoveries import client, configure_tmdb, fake_tmdb, login, scan  # noqa: F401


def test_a_change_maps_to_its_top_level_folder(tmp_path: Path) -> None:
    from app.services.media_watcher import top_level_folder

    movies, shows = tmp_path / "movies", tmp_path / "shows"
    (movies / "Dune.2021.2160p-GRP").mkdir(parents=True)
    roots = {uuid.uuid4(): movies, uuid.uuid4(): shows}
    movie_root, show_root = list(roots)
    assert top_level_folder(str(movies / "Dune.2021.2160p-GRP" / "Dune.mkv"), roots) == (movie_root, str(movies / "Dune.2021.2160p-GRP"))
    # A deleted folder with a dotted name is still a folder to check.
    assert top_level_folder(str(movies / "Gone.2019.1080p-GRP"), roots) == (movie_root, str(movies / "Gone.2019.1080p-GRP"))
    assert top_level_folder(str(shows / "Show" / "Season 01" / "e01.mkv"), roots) == (show_root, str(shows / "Show"))
    assert top_level_folder(str(movies), roots) is None
    assert top_level_folder(str(tmp_path / "elsewhere" / "x.mkv"), roots) is None


def _root(client: TestClient, headers, path: Path) -> str:
    created = client.post("/api/v1/storage-roots", headers=headers, json={
        "name": f"Watched {uuid.uuid4().hex[:6]}", "path": str(path), "media_type": "movies", "discovery_mode": "review",
    })
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_checking_one_folder_finds_new_media_and_confirms_a_deletion_after_the_grace(client: TestClient, tmp_path: Path) -> None:
    from app.services.media_watcher import refresh_media_folder

    headers = login(client)
    configure_tmdb(client, headers)
    root = tmp_path / "movies"
    kept = root / "Dune.2021.2160p.UHD.BluRay"
    kept.mkdir(parents=True)
    (kept / "Dune.mkv").write_bytes(b"movie")
    root_id = _root(client, headers, root)
    scan(client, headers, root_id)
    assert client.get("/api/v1/discoveries/count").json()["count"] == 1

    # A folder copied in after the scan: one check of that folder lists it.
    added = root / "Arrival.2016.1080p.BluRay"
    added.mkdir()
    (added / "Arrival.mkv").write_bytes(b"movie")
    assert asyncio.run(refresh_media_folder(uuid.UUID(root_id), str(added.resolve()))) is False
    assert client.get("/api/v1/discoveries/count").json()["count"] == 2

    # Deleted by hand: missing only once the grace is used up, rechecked meanwhile.
    shutil.rmtree(kept)
    checks = [asyncio.run(refresh_media_folder(uuid.UUID(root_id), str(kept.resolve()))) for _ in range(4)]
    assert checks[0] is True
    assert checks[-1] is False
    assert client.get("/api/v1/discoveries/count").json()["count"] == 1


def test_the_watcher_lists_a_folder_copied_into_a_media_folder(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services import media_watcher

    headers = login(client)
    configure_tmdb(client, headers)
    root = tmp_path / "movies"
    root.mkdir()
    root_id = _root(client, headers, root)
    scan(client, headers, root_id)
    monkeypatch.setattr(get_settings(), "watch_media_folders", True)
    monkeypatch.setattr(media_watcher, "SETTLE_SECONDS", 0.5)
    monkeypatch.setattr(media_watcher, "TICK_SECONDS", 0.2)

    async def scenario() -> int:
        stop = asyncio.Event()
        task = asyncio.create_task(media_watcher.run_media_watcher(stop))
        try:
            deadline = time.monotonic() + 10
            while media_watcher.watch_state(uuid.UUID(root_id)) != "watching" and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.5)
            folder = root / "Dune.2021.2160p.UHD.BluRay"
            folder.mkdir()
            (folder / "Dune.mkv").write_bytes(b"movie")
            from app.db import session as db_session
            from sqlalchemy import func, select
            from app.models.domain import MediaDiscovery
            while time.monotonic() < deadline + 10:
                async with db_session.async_session_factory() as db:
                    found = await db.scalar(select(func.count()).select_from(MediaDiscovery))
                if found:
                    return int(found)
                await asyncio.sleep(0.2)
            return 0
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=10)

    assert asyncio.run(scenario()) == 1
