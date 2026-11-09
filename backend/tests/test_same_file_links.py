"""Linking a torrent to files already on disk, instead of downloading them."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db import session as db_session
from app.integrations.qbittorrent import TorrentCategory, torrent_file_listing, torrent_payload_info_hash
from app.models.domain import (
    AssociationType,
    MediaFile,
    MediaRole,
    MediaType,
    MovieReleaseTorrent,
    Torrent,
    TorrentClientObservation,
    UpgradeCandidate,
)
from app.services.integration_state import get_configured_download_client
from app.services.same_file_links import (
    local_to_remote_path,
    note_same_file_results,
    settle_same_file_links,
    submit_same_file_link,
)
from tests.test_upgrade_execution import _create_integrations, _login, _seed_movie_upgrade, client  # noqa: F401

FILE_NAME = "Upgrade.Movie.2024.1080p.WEB-DL-GRP.mkv"
RELEASE_NAME = "Upgrade.Movie.2024.1080p.WEB-DL-GRP"
ON_DISK = len(b"old-video")


def _torrent(name: str, length: int) -> bytes:
    return b"d4:info" + f"d6:lengthi{length}e4:name{len(name)}:{name}e".encode() + b"e"


class _QBit:
    def __init__(self):
        self.added: list[dict] = []
        self.calls: list[tuple] = []

    def factory(self, *_args, **_kwargs):
        return self

    async def list_categories(self):
        return [TorrentCategory(name="movies", save_path="/downloads", resolved_save_path="/downloads")]

    async def get_torrent(self, _info_hash):
        return None

    async def add_torrent(self, _payload, **options):
        self.added.append(options)

    async def recheck_torrents(self, hashes):
        self.calls.append(("recheck", *hashes))

    async def start_torrents(self, hashes):
        self.calls.append(("start", *hashes))

    async def remove_torrent(self, info_hash, *, delete_files=False):
        self.calls.append(("remove", info_hash, delete_files))

    async def close(self):
        return None


class _Indexer:
    torznab_url = "http://indexer.test"
    api_key = "key"
    timeout_seconds = 5
    id = "indexer"

    def __init__(self, payloads):
        self.payloads = payloads

    def __call__(self, *_args, **_kwargs):
        return self

    async def fetch_torrent(self, url):
        return self.payloads[url]

    async def close(self):
        return None


URL = "http://indexer.test/file.torrent"


async def _seed(tmp_path: Path, qbit: dict, indexer: dict):
    """A title whose only file is on disk and has no torrent, with a linkable result."""

    candidate_id, _movie, release_id, _torrent_id, old_dir, _new = await _seed_movie_upgrade(tmp_path, qbit, indexer)
    async with db_session.async_session_factory() as db:
        for link in (await db.scalars(select(MovieReleaseTorrent).where(MovieReleaseTorrent.movie_release_id == release_id))).all():
            await db.delete(link)
        media_file = await db.scalar(select(MediaFile).where(MediaFile.filename == FILE_NAME))
        media_file.media_role = MediaRole.MOVIE_VIDEO
        row = await db.get(UpgradeCandidate, candidate_id)
        row.target_key = f"replace:movie:{release_id}"
        row.download_url = URL
        row.comparison_snapshot = {"same_file": True}
        await db.commit()
    return candidate_id, release_id, old_dir


async def _submit(candidate_id, qbit: dict, payload: bytes, fake: _QBit) -> UpgradeCandidate:
    indexer = _Indexer({URL: payload})
    async with db_session.async_session_factory() as db:
        row = await db.get(UpgradeCandidate, candidate_id)
        configured = await get_configured_download_client(db, UUID(qbit["id"]))
        await submit_same_file_link(
            db, row, client=configured, indexer=indexer, category="movies",
            torznab_client_factory=indexer, qbittorrent_client_factory=fake.factory,
        )
        await db.commit()
        return row


def test_same_file_is_added_stopped_over_the_existing_folder_and_checked(client) -> None:  # noqa: F811
    test_client, tmp_path = client
    qbit, indexer = _create_integrations(test_client, _login(test_client))
    candidate_id, _release, old_dir = asyncio.run(_seed(tmp_path, qbit, indexer))
    payload = _torrent(FILE_NAME, ON_DISK)
    fake = _QBit()
    assert asyncio.run(_submit(candidate_id, qbit, payload, fake)).execution_state == "linking"
    assert fake.added[0]["stopped"] is True
    assert fake.added[0]["auto_tmm"] is False
    assert fake.added[0]["content_layout"] == "nosubfolder"
    assert Path(fake.added[0]["save_path"]) == old_dir
    assert fake.calls == [("recheck", torrent_payload_info_hash(payload))]


def test_a_file_that_changed_size_is_not_linked(client) -> None:  # noqa: F811
    test_client, tmp_path = client
    qbit, indexer = _create_integrations(test_client, _login(test_client))
    candidate_id, *_ = asyncio.run(_seed(tmp_path, qbit, indexer))
    fake = _QBit()
    with pytest.raises(AppError, match="no longer matches"):
        asyncio.run(_submit(candidate_id, qbit, _torrent(FILE_NAME, 123456), fake))
    assert fake.added == []


async def _start_linking(candidate_id, qbit, *, progress, state, minutes_ago=5) -> UUID:
    async with db_session.async_session_factory() as db:
        torrent = Torrent(info_hash="d" * 40, name="linked")
        db.add(torrent)
        await db.flush()
        db.add(TorrentClientObservation(
            torrent_id=torrent.id, download_client_id=UUID(qbit["id"]), state=state, progress=progress, is_present=True,
        ))
        row = await db.get(UpgradeCandidate, candidate_id)
        row.execution_state = "linking"
        row.torrent_id = torrent.id
        row.submitted_info_hash = "d" * 40
        row.selected_download_client_id = UUID(qbit["id"])
        row.submitted_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        await db.commit()
        return torrent.id


async def _settle(qbit, fake) -> None:
    async with db_session.async_session_factory() as db:
        configured = await get_configured_download_client(db, UUID(qbit["id"]))
        await settle_same_file_links(db, configured, client_factory=fake.factory)
        await db.commit()


def test_a_complete_check_starts_the_torrent_and_links_it_to_the_release(client) -> None:  # noqa: F811
    test_client, tmp_path = client
    qbit, indexer = _create_integrations(test_client, _login(test_client))
    candidate_id, release_id, _dir = asyncio.run(_seed(tmp_path, qbit, indexer))
    torrent_id = asyncio.run(_start_linking(candidate_id, qbit, progress=1, state="stoppedUP", minutes_ago=0))
    fake = _QBit()
    asyncio.run(_settle(qbit, fake))
    assert fake.calls == [("start", "d" * 40)]

    async def check():
        async with db_session.async_session_factory() as db:
            assert (await db.get(UpgradeCandidate, candidate_id)).execution_state == "completed"
            link = await db.scalar(select(MovieReleaseTorrent).where(MovieReleaseTorrent.torrent_id == torrent_id))
            assert link.movie_release_id == release_id and link.association_type == AssociationType.ATTACHED

    asyncio.run(check())


@pytest.mark.parametrize(("progress", "state", "minutes_ago"), [(0.4, "checkingUP", 5), (0, "stoppedDL", 0)])
def test_nothing_is_decided_until_the_check_has_finished(client, progress, state, minutes_ago) -> None:  # noqa: F811
    test_client, tmp_path = client
    qbit, indexer = _create_integrations(test_client, _login(test_client))
    candidate_id, *_ = asyncio.run(_seed(tmp_path, qbit, indexer))
    asyncio.run(_start_linking(candidate_id, qbit, progress=progress, state=state, minutes_ago=minutes_ago))
    fake = _QBit()
    asyncio.run(_settle(qbit, fake))
    assert fake.calls == []


def test_a_short_check_removes_the_torrent_keeps_the_files_and_is_remembered(client) -> None:  # noqa: F811
    test_client, tmp_path = client
    qbit, indexer = _create_integrations(test_client, _login(test_client))
    candidate_id, _release, old_dir = asyncio.run(_seed(tmp_path, qbit, indexer))
    torrent_id = asyncio.run(_start_linking(candidate_id, qbit, progress=0.97, state="stoppedDL"))
    fake = _QBit()
    asyncio.run(_settle(qbit, fake))
    assert fake.calls == [("remove", "d" * 40, False)]
    assert (old_dir / FILE_NAME).exists()

    async def check():
        async with db_session.async_session_factory() as db:
            row = await db.get(UpgradeCandidate, candidate_id)
            assert row.execution_state is None
            assert row.comparison_snapshot.get("link_failed") is True
            assert "same_file" not in row.comparison_snapshot
            assert (await db.get(Torrent, torrent_id)).metadata_json["link_check_failed"] is True
            assert await db.scalar(select(MovieReleaseTorrent).where(MovieReleaseTorrent.torrent_id == torrent_id)) is None

    asyncio.run(check())


def _target(release_id):
    return SimpleNamespace(
        media_type=MediaType.MOVIES,
        releases=[SimpleNamespace(release_id=release_id)],
        reference=SimpleNamespace(release_name=RELEASE_NAME),
    )


def _result(url):
    return {"indexer_id": "indexer", "eligible": True, "title": RELEASE_NAME, "download_url": url}


async def _note(release_id, results, payloads) -> None:
    indexer = _Indexer(payloads)
    async with db_session.async_session_factory() as db:
        await note_same_file_results(db, target=_target(release_id), results=results, indexers=[indexer], client_factory=indexer)


def test_search_marks_only_the_result_whose_files_match(client) -> None:  # noqa: F811
    test_client, tmp_path = client
    qbit, indexer = _create_integrations(test_client, _login(test_client))
    _candidate, release_id, _dir = asyncio.run(_seed(tmp_path, qbit, indexer))
    results = [_result("http://x/other"), _result("http://x/good"), _result("magnet:?xt=urn:btih:" + "e" * 40)]
    payloads = {"http://x/other": _torrent(FILE_NAME, 77), "http://x/good": _torrent(FILE_NAME, ON_DISK)}
    asyncio.run(_note(release_id, results, payloads))
    assert [bool(item.get("same_file")) for item in results] == [False, True, False]


def test_a_torrent_that_failed_its_check_is_not_offered_as_the_same_file_again(client) -> None:  # noqa: F811
    test_client, tmp_path = client
    qbit, indexer = _create_integrations(test_client, _login(test_client))
    _candidate, release_id, _dir = asyncio.run(_seed(tmp_path, qbit, indexer))
    payload = _torrent(FILE_NAME, ON_DISK)

    async def remember():
        async with db_session.async_session_factory() as db:
            db.add(Torrent(info_hash=torrent_payload_info_hash(payload), name="x", metadata_json={"link_check_failed": True}))
            await db.commit()

    asyncio.run(remember())
    results = [_result("http://x/good")]
    asyncio.run(_note(release_id, results, {"http://x/good": payload}))
    assert "same_file" not in results[0]
    assert results[0]["link_failed"] is True


@pytest.mark.parametrize(("best_score", "same_score", "picked"), [(50, 50, "same"), (80, 50, "best")])
def test_the_same_file_wins_a_tie_but_never_beats_a_better_release(client, best_score, same_score, picked) -> None:  # noqa: F811
    from app.services.same_file_links import apply_same_file_results

    test_client, tmp_path = client
    qbit, indexer = _create_integrations(test_client, _login(test_client))
    candidate_id, *_ = asyncio.run(_seed(tmp_path, qbit, indexer))

    def result(guid, score, **extra):
        return {"id": guid, "guid": guid, "indexer_id": indexer["id"], "indexer_name": "x", "eligible": True, "title": guid,
                "download_url": f"http://x/{guid}", "quality": "2160p WEB-DL", "custom_format_score": score,
                "snapshot": {"current": {"custom_format_score": 0}}, **extra}

    results = [result("best", best_score), result("same", same_score, same_file=True)]

    async def run():
        async with db_session.async_session_factory() as db:
            row = await db.get(UpgradeCandidate, candidate_id)
            row.guid, row.custom_format_score, row.quality, row.comparison_snapshot = "best", best_score, "2160p WEB-DL", {}
            await apply_same_file_results(db, row.target_key, results)
            return row.guid, row.comparison_snapshot.get("same_file")

    assert asyncio.run(run()) == (picked, True if picked == "same" else None)


def test_torrent_file_listing_reads_single_and_multi_file_torrents() -> None:
    assert torrent_file_listing(b"d4:infod6:lengthi9e4:name5:a.mkvee") == [("a.mkv", 9)]
    multi = b"d4:infod5:filesld6:lengthi3e4:pathl3:sub5:b.mkveed4:attr1:p6:lengthi1e4:pathl4:.pad1:0eee4:name3:topee"
    assert torrent_file_listing(multi) == [("sub/b.mkv", 3)]
    assert torrent_file_listing(b"not a torrent") is None
    assert torrent_file_listing(b"d4:infod5:filesld6:lengthi3e4:pathl2:..5:b.mkveee4:name3:topee") is None


def test_a_folder_is_translated_for_qbittorrent() -> None:
    mapping = SimpleNamespace(local_prefix="/media/movies", remote_prefix="D:\\Movies")
    assert local_to_remote_path("/media/movies/Foo (2010)", [mapping]) == "D:\\Movies\\Foo (2010)"
    assert local_to_remote_path("/elsewhere/Foo", [mapping]) is None
    assert local_to_remote_path("/data/Foo", []) == "/data/Foo"
