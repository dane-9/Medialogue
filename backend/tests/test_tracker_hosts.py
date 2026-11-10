"""Tracing a torrent Medialogue did not grab to the indexer behind its tracker."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import session as db_session
from app.integrations.qbittorrent import magnet_announce_hosts, torrent_announce_hosts
from app.integrations.torznab import SearchResult
from app.models.domain import IndexerTrackerHost
from app.services import release_source, tracker_hosts
from app.services.release_source import SourceLookup, indexers_by_tracker_host


def _bencoded(url: bytes) -> bytes:
    return b"%d:%s" % (len(url), url)


def _torrent(announce: bytes, *more: bytes) -> bytes:
    payload = b"d8:announce" + _bencoded(announce)
    if more:
        payload += b"13:announce-listl" + b"".join(b"l" + _bencoded(url) + b"e" for url in more) + b"e"
    return payload + b"4:infod4:name1:xee"


def _release(name: str, *, size: int | None = 1000, url: str | None = "https://prowlarr/dl") -> SearchResult:
    return SearchResult(guid=name, title=name, download_url=url and f"{url}/{name}" if url and not url.startswith("magnet:") else url,
                        size=size, seeders=5, published=None)


def _indexer(name: str = "Aither"):
    return SimpleNamespace(id=uuid4(), name=name, torznab_url="https://prowlarr/1/api", api_key="key", timeout_seconds=5)


def test_a_torrent_gives_its_hosts_and_never_its_passkey() -> None:
    payload = _torrent(b"https://aither.cc/announce/SECRETKEY", b"https://aither.cc/announce/SECRETKEY", b"udp://Backup.Aither.cc:6969/SECRETKEY")
    assert torrent_announce_hosts(payload) == ("aither.cc", "backup.aither.cc")


@pytest.mark.parametrize("payload", [b"", b"<html>not a torrent</html>", b"d8:announce", b"d8:announce99:short"])
def test_a_file_that_is_not_a_torrent_gives_nothing(payload: bytes) -> None:
    assert torrent_announce_hosts(payload) == ()


def test_a_magnet_names_its_hosts() -> None:
    assert magnet_announce_hosts("magnet:?xt=urn:btih:abc&tr=https%3A%2F%2Faither.cc%2Fannounce%2FKEY") == ("aither.cc",)
    assert magnet_announce_hosts("magnet:?xt=urn:btih:abc") == ()


def test_the_smallest_release_is_the_sample() -> None:
    chosen = tracker_hosts.sample_release([_release("big", size=90), _release("unknown", size=None), _release("small", size=10), _release("none", url=None)])
    assert chosen is not None and chosen.title == "small"


def test_a_magnet_with_trackers_needs_no_download() -> None:
    magnet = _release("magnet", size=90, url="magnet:?xt=urn:btih:abc&tr=https%3A%2F%2Faither.cc%2Fa")
    chosen = tracker_hosts.sample_release([_release("file", size=1), magnet])
    assert chosen is magnet


def _torrent_row(host: str | None, info_hash: str = "abc"):
    return SimpleNamespace(info_hash=info_hash, tracker_summary={"tracker": f"https://{host}:443/announce"} if host else {})


def test_a_known_tracker_names_its_indexer_and_grabs_still_win() -> None:
    owner = release_source.ReleaseSource(indexer_name="Aither", tracker="aither.cc")
    grabbed = release_source.ReleaseSource(indexer_name="From a search")
    trackers = {"aither.cc": owner}
    assert SourceLookup({}, {}, trackers).source([_torrent_row("aither.cc")]) is owner
    assert SourceLookup({}, {"abc": grabbed}, trackers).source([_torrent_row("aither.cc")]) is grabbed


def test_an_unknown_tracker_stays_a_bare_address() -> None:
    assert SourceLookup({}, {}, {}).source([_torrent_row("other.example")]) == release_source.ReleaseSource(tracker="other.example")


class _Client:
    fetched: list[str] = []
    payload = _torrent(b"https://aither.cc/announce/SECRETKEY")

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def fetch_torrent(self, url: str) -> bytes:
        type(self).fetched.append(url)
        return type(self).payload

    async def close(self) -> None:
        pass


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hosts.db'}", poolclass=NullPool)

    async def create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(IndexerTrackerHost.__table__.create)

    asyncio.run(create())
    monkeypatch.setattr(db_session, "async_session_factory", async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(_Client, "fetched", [])

    rescores: list[bool] = []

    async def rescore() -> None:
        rescores.append(True)

    monkeypatch.setattr(tracker_hosts, "rescore_library_after_save", rescore)
    return rescores


async def _stored() -> list[tuple]:
    async with db_session.async_session_factory() as db:
        return [tuple(row) for row in (await db.execute(select(IndexerTrackerHost.indexer_id, IndexerTrackerHost.host))).all()]


def test_one_file_is_read_once_and_only_the_host_is_kept(database) -> None:
    indexer = _indexer()
    results = [_release("big", size=90), _release("small", size=10)]

    async def scenario() -> list[tuple]:
        await tracker_hosts.sample_new_indexers([(indexer, results)], client_factory=_Client)
        await tracker_hosts.sample_new_indexers([(indexer, results)], client_factory=_Client)
        return await _stored()

    assert asyncio.run(scenario()) == [(indexer.id, "aither.cc")]
    assert _Client.fetched == ["https://prowlarr/dl/small"]
    assert database == [True]


def test_a_failed_sample_waits_and_breaks_nothing(database) -> None:
    indexer = _indexer("Broken")

    class Failing(_Client):
        async def fetch_torrent(self, url: str) -> bytes:
            type(self).fetched.append(url)
            raise RuntimeError("tracker is down")

    monkeypatch_list: list[str] = []
    Failing.fetched = monkeypatch_list

    async def scenario() -> list[tuple]:
        await tracker_hosts.sample_new_indexers([(indexer, [_release("one")])], client_factory=Failing)
        await tracker_hosts.sample_new_indexers([(indexer, [_release("one")])], client_factory=Failing)
        return await _stored()

    assert asyncio.run(scenario()) == []
    assert len(monkeypatch_list) == 1
    assert database == []


def test_a_host_two_indexers_claim_names_neither_and_removed_indexers_are_ignored(database, monkeypatch) -> None:
    first, second, only, removed = uuid4(), uuid4(), uuid4(), uuid4()
    configs = [SimpleNamespace(id=first, name="One"), SimpleNamespace(id=second, name="Two"), SimpleNamespace(id=only, name="Only")]
    monkeypatch.setattr(release_source, "get_integration_config_store", lambda: SimpleNamespace(list_indexers=lambda: configs))

    async def scenario() -> dict:
        async with db_session.async_session_factory() as db:
            await tracker_hosts.remember_tracker_hosts(db, first, ("shared.example",))
            await tracker_hosts.remember_tracker_hosts(db, second, ("shared.example",))
            await tracker_hosts.remember_tracker_hosts(db, only, ("only.example",))
            await tracker_hosts.remember_tracker_hosts(db, removed, ("gone.example",))
            await tracker_hosts.remember_tracker_hosts(db, only, ("only.example",))
            await db.commit()
            return await indexers_by_tracker_host(db)

    owners = asyncio.run(scenario())
    assert set(owners) == {"only.example"}
    assert owners["only.example"].indexer_name == "Only"
