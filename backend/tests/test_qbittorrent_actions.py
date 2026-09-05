import hashlib
from urllib.parse import parse_qs

import httpx
import pytest

from app.integrations.qbittorrent import QBittorrentClient, QBittorrentError


@pytest.mark.parametrize("kind", ["magnet", "file"])
@pytest.mark.parametrize("failure", ["priority", "read"])
async def test_accepted_submission_survives_priority_failure(kind, failure):
    info = b"d4:name4:test6:lengthi1ee"
    payload = b"d4:info" + info + b"e"
    info_hash = hashlib.sha1(info).hexdigest()
    adds = []

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/add"):
            adds.append(request)
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/info"):
            if failure == "read":
                raise httpx.ReadTimeout("Timed out", request=request)
            return httpx.Response(200, json=[{"hash": info_hash}])
        assert request.url.path.endswith("/topPrio")
        return httpx.Response(409)

    async with QBittorrentClient("http://qbit", "u", "p", transport=httpx.MockTransport(handler)) as client:
        if kind == "magnet":
            await client.add_url(f"magnet:?xt=urn:btih:{info_hash}", add_to_top=True)
        else:
            await client.add_torrent(payload, add_to_top=True)
    assert len(adds) == 1


async def test_rejected_add_still_fails():
    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        assert request.url.path.endswith("/add")
        return httpx.Response(415)

    async with QBittorrentClient("http://qbit", "u", "p", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(QBittorrentError):
            await client.add_url("magnet:?xt=urn:btih:" + "a" * 40, add_to_top=True)


async def test_speed_limits_use_mutating_endpoints():
    calls = []

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        calls.append((request.url.path, parse_qs(request.content.decode())))
        return httpx.Response(200)

    async with QBittorrentClient("http://qbit", "u", "p", transport=httpx.MockTransport(handler)) as client:
        await client.set_download_limit(["ABC"], 1024)
        await client.set_upload_limit(["ABC"], 0)
    assert calls == [
        ("/api/v2/torrents/setDownloadLimit", {"hashes": ["abc"], "limit": ["1024"]}),
        ("/api/v2/torrents/setUploadLimit", {"hashes": ["abc"], "limit": ["0"]}),
    ]


@pytest.mark.parametrize("ratio", [-2, -1, 1.5])
async def test_ratio_changes_preserve_each_torrents_other_limits(ratio):
    calls = []

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/info"):
            assert request.url.params["hashes"] == "abc|def"
            return httpx.Response(200, json=[
                {"hash": "abc", "seeding_time_limit": 120, "inactive_seeding_time_limit": 30,
                 "share_limit_action": 0, "share_limits_mode": 1},
                {"hash": "def", "seeding_time_limit": -1, "inactive_seeding_time_limit": -2},
            ])
        assert request.url.path.endswith("/setShareLimits")
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200)

    async with QBittorrentClient("http://qbit", "u", "p", transport=httpx.MockTransport(handler)) as client:
        await client.set_share_limits(["ABC", "DEF"], ratio)
    assert calls == [
        {"hashes": ["abc"], "ratioLimit": [str(ratio)], "seedingTimeLimit": ["120"],
         "inactiveSeedingTimeLimit": ["30"], "shareLimitAction": ["0"], "shareLimitsMode": ["1"]},
        {"hashes": ["def"], "ratioLimit": [str(ratio)], "seedingTimeLimit": ["-1"],
         "inactiveSeedingTimeLimit": ["-2"]},
    ]


async def test_missing_time_limits_do_not_overwrite_settings():
    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="Ok.")
        assert request.method == "GET"
        return httpx.Response(200, json=[{"hash": "abc"}])

    async with QBittorrentClient("http://qbit", "u", "p", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(QBittorrentError, match="existing seeding time limits"):
            await client.set_share_limits(["abc"], 2)
