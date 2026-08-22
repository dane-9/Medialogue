import httpx
import pytest

from app.integrations.plex import PlexClient, PlexLibrarySnapshot, PlexMediaMatch
from app.integrations.qbittorrent import QBittorrentAuthError, QBittorrentClient, QBittorrentError
from app.integrations.torznab import TorznabClient


@pytest.mark.asyncio
async def test_qbittorrent_observation_and_completion():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, text="Ok.")
        return httpx.Response(
            200,
            json=[
                {
                    "hash": "ABC",
                    "name": "Inception",
                    "progress": 1,
                    "state": "pausedUP",
                    "save_path": "/downloads/movies",
                    "content_path": "/downloads/movies/Inception",
                    "tags": "managed, archive",
                }
            ],
        )

    client = QBittorrentClient("http://qbit", "user", "pass", transport=httpx.MockTransport(handler))
    values = await client.list_torrents()
    await client.close()
    assert values[0].info_hash == "abc"
    assert values[0].complete
    assert values[0].tags == ("managed", "archive")


@pytest.mark.parametrize("state", ["checkingUP", "checkingDL", "checkingResumeData"])
def test_qbittorrent_checking_states_are_transient_not_complete(state: str):
    from app.integrations.qbittorrent import TorrentObservation

    observation = TorrentObservation(
        info_hash="abc",
        name="Checking",
        progress=1.0,
        state=state,
        save_path="/downloads",
        content_path="/downloads/Checking",
        category="",
        tags=(),
        tracker=None,
        total_size=1,
        added_at=None,
        completed_at=1_700_000_000,
    )

    assert observation.checking is True
    assert observation.complete is False


@pytest.mark.asyncio
async def test_qbittorrent_accepts_v52_no_content_login_response():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(204)
        if request.url.path.endswith("/app/version"):
            return httpx.Response(200, text="v5.2.0")
        raise AssertionError(request.url.path)

    client = QBittorrentClient(
        "http://qbit",
        "user",
        "pass",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await client.health() == {"status": "healthy", "version": "v5.2.0"}
    finally:
        await client.close()

    assert paths == ["/api/v2/auth/login", "/api/v2/app/version"]


@pytest.mark.asyncio
async def test_qbittorrent_login_closes_auth_socket_but_keeps_session_cookie():
    calls: list[tuple[str, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("connection"), request.headers.get("cookie")))
        if request.url.path.endswith("/auth/login"):
            assert request.headers.get("connection") == "close"
            return httpx.Response(204, headers={"set-cookie": "SID=session123; Path=/"})
        if request.url.path.endswith("/app/version"):
            assert "SID=session123" in (request.headers.get("cookie") or "")
            return httpx.Response(200, text="v5.2.0")
        raise AssertionError(request.url.path)

    client = QBittorrentClient("http://qbit", "user", "pass", transport=httpx.MockTransport(handler))
    try:
        assert await client.health() == {"status": "healthy", "version": "v5.2.0"}
    finally:
        await client.close()

    assert [path for path, _, _ in calls] == ["/api/v2/auth/login", "/api/v2/app/version"]


@pytest.mark.asyncio
async def test_qbittorrent_retries_one_transient_disconnect_for_read_only_request():
    reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(204, headers={"set-cookie": "SID=session123; Path=/"})
        if request.url.path.endswith("/app/version"):
            reads += 1
            if reads == 1:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return httpx.Response(200, text="v5.2.0")
        raise AssertionError(request.url.path)

    client = QBittorrentClient("http://qbit", "user", "pass", transport=httpx.MockTransport(handler))
    try:
        assert await client.health() == {"status": "healthy", "version": "v5.2.0"}
    finally:
        await client.close()

    assert reads == 2


@pytest.mark.asyncio
async def test_qbittorrent_does_not_retry_mutating_request_after_disconnect():
    adds = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal adds
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(204, headers={"set-cookie": "SID=session123; Path=/"})
        if request.url.path.endswith("/torrents/add"):
            adds += 1
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        raise AssertionError(request.url.path)

    client = QBittorrentClient("http://qbit", "user", "pass", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(QBittorrentError, match="connection failed during POST"):
            await client.add_url("magnet:?xt=urn:btih:abc")
    finally:
        await client.close()

    assert adds == 1


def test_qbittorrent_new_client_polling_default_is_30_seconds():
    from app.schemas.downloads import DownloadClientCreate

    payload = DownloadClientCreate(
        name="qbit",
        url="http://qbit:8080",
        password="secret",
        scope="movies",
    )
    assert payload.poll_interval_seconds == 30


@pytest.mark.asyncio
async def test_qbittorrent_auth_errors_distinguish_bad_credentials_from_ip_ban():
    def rejected_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/auth/login"
        return httpx.Response(200, text="Fails.")

    rejected = QBittorrentClient(
        "http://qbit", "user", "wrong", transport=httpx.MockTransport(rejected_handler)
    )
    try:
        with pytest.raises(QBittorrentAuthError, match="rejected the configured username/password") as exc_info:
            await rejected.health()
        assert exc_info.value.reason == "credentials_rejected"
    finally:
        await rejected.close()

    def banned_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/auth/login"
        return httpx.Response(
            403,
            text="Your IP address has been banned after too many failed authentication attempts.",
        )

    banned = QBittorrentClient(
        "http://qbit", "user", "correct", transport=httpx.MockTransport(banned_handler)
    )
    try:
        with pytest.raises(QBittorrentAuthError, match="temporarily banned Medialogue's IP") as exc_info:
            await banned.health()
        assert exc_info.value.reason == "ip_banned"
    finally:
        await banned.close()


@pytest.mark.asyncio
async def test_qbittorrent_preserves_reverse_proxy_base_path():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/app/version"):
            return httpx.Response(200, text="v5.2.0")
        raise AssertionError(request.url.path)

    client = QBittorrentClient(
        "http://qbit.example/qbit",
        "user",
        "pass",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await client.health() == {"status": "healthy", "version": "v5.2.0"}
    finally:
        await client.close()

    assert paths == ["/qbit/api/v2/auth/login", "/qbit/api/v2/app/version"]


@pytest.mark.asyncio
async def test_qbittorrent_read_and_control_actions_use_expected_api_fields():
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/app/version"):
            return httpx.Response(200, text="v4.6.4")
        if request.url.path.endswith("/torrents/info"):
            assert request.url.params.get("hashes") == "abc"
            return httpx.Response(
                200,
                json=[
                    {
                        "hash": "ABC",
                        "name": "Inception 2010",
                        "progress": 0.42,
                        "state": "downloading",
                        "save_path": "/downloads/movies",
                        "content_path": "/downloads/movies/Inception 2010",
                        "category": "movies",
                        "tags": "managed",
                        "tracker": "https://tracker.example/announce",
                        "size": 42,
                        "added_on": 10,
                    }
                ],
            )
        if request.url.path.endswith("/torrents/export"):
            assert request.method == "GET"
            assert request.url.params.get("hash") == "abc"
            return httpx.Response(200, content=b"torrent-export-bytes")
        if request.url.path.endswith("/torrents/add"):
            assert request.method == "POST"
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/torrents/delete"):
            assert request.method == "POST"
            return httpx.Response(200, text="Ok.")
        raise AssertionError(f"Unexpected qBittorrent request: {request.method} {request.url}")

    client = QBittorrentClient("http://qbit", "user", "pass", transport=httpx.MockTransport(handler))
    try:
        health = await client.health()
        assert health == {"status": "healthy", "version": "v4.6.4"}
        item = await client.get_torrent("ABC")
        assert item is not None
        assert item.info_hash == "abc"
        assert item.progress == pytest.approx(0.42)
        exported = await client.export_torrent("ABC")
        assert exported == b"torrent-export-bytes"
        await client.add_torrent(
            b"torrent-bytes",
            filename="inception.torrent",
            save_path="/downloads/movies",
            category="movies",
            tags=("managed", "archive"),
        )
        await client.remove_torrent("ABC", delete_files=True)
    finally:
        await client.close()

    paths = [path for _, path, _ in calls]
    assert "/api/v2/app/version" in paths
    assert "/api/v2/torrents/info" in paths
    assert "/api/v2/torrents/export" in paths
    assert "/api/v2/torrents/add" in paths
    assert "/api/v2/torrents/delete" in paths


@pytest.mark.asyncio
async def test_plex_exact_path_match_is_strong_evidence():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/library/sections":
            return httpx.Response(200, text='<MediaContainer><Directory key="1" /></MediaContainer>')
        return httpx.Response(
            200,
            text=(
                '<MediaContainer><Video ratingKey="10" title="Inception" year="2010">'
                '<Media><Part file="/media/movies/Inception/movie.mkv" /></Media>'
                "</Video></MediaContainer>"
            ),
        )

    client = PlexClient("http://plex", "token", transport=httpx.MockTransport(handler))
    match = await client.find_exact_path("/media/movies/Inception/movie.mkv")
    await client.close()
    assert match is not None
    assert match.rating_key == "10"
    assert match.year == 2010


def test_plex_snapshot_matches_storage_root_relative_path_across_container_mounts():
    snapshot = PlexLibrarySnapshot(items=(
        PlexMediaMatch(
            rating_key="movie-1",
            title="Inception",
            year=2010,
            edition=None,
            file_path="/plex-media/movies/Inception 2010/movie.mkv",
        ),
        PlexMediaMatch(
            rating_key="episode-1",
            title="Episode One",
            year=2019,
            edition=None,
            file_path="/plex-media/tv/Dollface 2019/Season 01/Dollface S01E01.mkv",
            show_title="Dollface",
            season_number=1,
            episode_number=1,
        ),
    ))

    movie = snapshot.find_exact_path(
        "/movies/Inception 2010/movie.mkv",
        local_root="/movies",
        media_type="movies",
    )
    episode = snapshot.find_exact_path(
        "/shows/Dollface 2019/Season 01/Dollface S01E01.mkv",
        local_root="/shows",
        media_type="shows",
    )

    assert movie is not None and movie.rating_key == "movie-1"
    assert episode is not None and episode.rating_key == "episode-1"


def test_plex_snapshot_refuses_ambiguous_relative_path_match():
    snapshot = PlexLibrarySnapshot(items=(
        PlexMediaMatch("a", "Same", 2020, None, "/library-a/Same 2020/movie.mkv"),
        PlexMediaMatch("b", "Same", 2020, None, "/library-b/Same 2020/movie.mkv"),
    ))

    assert snapshot.find_exact_path(
        "/movies/Same 2020/movie.mkv",
        local_root="/movies",
        media_type="movies",
    ) is None


@pytest.mark.asyncio
async def test_torznab_result_parsing():
    xml = '''<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item>
      <title>Inception 2010 2160p</title><guid>g1</guid>
      <enclosure url="http://indexer/download/1" length="123" />
      <torznab:attr name="seeders" value="42" />
    </item></channel></rss>'''
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=xml))
    client = TorznabClient("http://indexer/api", "key", transport=transport)
    results = await client.search("Inception", media_type="movies", tmdb_id=27205)
    await client.close()
    assert results[0].guid == "g1"
    assert results[0].size == 123
    assert results[0].seeders == 42


def test_plex_snapshot_title_lookup_ignores_punctuation_and_ampersand_variants() -> None:
    from app.integrations.plex import PlexLibrarySnapshot, PlexMediaMatch

    snapshot = PlexLibrarySnapshot(
        items=(
            PlexMediaMatch(
                rating_key="1",
                title="Oliver & Company",
                year=1988,
                edition=None,
                file_path="/plex/movies/Oliver & Company/movie.mkv",
            ),
        )
    )

    matches = snapshot.search_title_year("Oliver and Company", 1988)
    assert len(matches) == 1
    assert matches[0].rating_key == "1"
