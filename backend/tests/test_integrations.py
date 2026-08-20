import httpx
import pytest

from app.integrations.plex import PlexClient, PlexLibrarySnapshot, PlexMediaMatch
from app.integrations.qbittorrent import QBittorrentClient
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
