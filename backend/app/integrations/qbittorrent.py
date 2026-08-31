from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


def _bencode_value_end(payload: bytes, position: int) -> int:
    token = payload[position:position + 1]
    if token == b"i":
        end = payload.index(b"e", position + 1)
        return end + 1
    if token in {b"l", b"d"}:
        cursor = position + 1
        while payload[cursor:cursor + 1] != b"e":
            cursor = _bencode_value_end(payload, cursor)
            if token == b"d":
                cursor = _bencode_value_end(payload, cursor)
        return cursor + 1
    colon = payload.index(b":", position)
    length = int(payload[position:colon])
    return colon + 1 + length


def _resolved_category_path(default_save_path: str, category_save_path: str) -> str:
    """Resolve a qBittorrent category path for display without overriding it.

    qBittorrent permits category paths relative to the client's default save
    directory. Keep Windows/UNC paths intact even when Medialogue itself runs
    on another platform; this value is informational only.
    """

    category = category_save_path.strip()
    default = default_save_path.strip()
    if not category:
        return default
    if category.startswith(("/", "\\")) or (len(category) >= 3 and category[1] == ":" and category[2] in {"/", "\\"}):
        return category
    if not default:
        return category
    separator = "\\" if "\\" in default and "/" not in default else "/"
    return f"{default.rstrip('/\\')}{separator}{category.lstrip('/\\')}"


def _torrent_info_hash(payload: bytes) -> str | None:
    try:
        if not payload.startswith(b"d"):
            return None
        cursor = 1
        while payload[cursor:cursor + 1] != b"e":
            key_end = _bencode_value_end(payload, cursor)
            colon = payload.index(b":", cursor)
            key = payload[colon + 1:key_end]
            value_start = key_end
            value_end = _bencode_value_end(payload, value_start)
            if key == b"info":
                return hashlib.sha1(payload[value_start:value_end]).hexdigest()
            cursor = value_end
    except (ValueError, IndexError):
        return None
    return None


def _magnet_info_hash(url: str) -> str | None:
    try:
        xt = next(
            (
                value
                for value in parse_qs(urlparse(url).query).get("xt", [])
                if value.casefold().startswith("urn:btih:")
            ),
            None,
        )
        if not xt:
            return None
        value = xt.rsplit(":", 1)[-1].strip()
        if len(value) == 40:
            bytes.fromhex(value)
            return value.lower()
        if len(value) == 32:
            return base64.b32decode(value.upper()).hex()
    except (ValueError, TypeError):
        return None
    return None


class QBittorrentError(RuntimeError):
    pass


class QBittorrentAuthError(QBittorrentError):
    """Authentication failure with enough detail for safe retry decisions."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class _TorrentPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hash: str = Field(min_length=1)
    name: str | None = None
    progress: float | None = None
    state: str | None = None
    save_path: str | None = None
    content_path: str | None = None
    category: str | None = None
    tags: str | None = None
    tracker: str | None = None
    total_size: int | None = None
    added_on: int | None = None
    completion_on: int | None = None
    amount_left: int | None = None
    auto_tmm: bool | None = None
    dl_limit: int | None = None
    up_limit: int | None = None
    downloaded: int | None = None
    uploaded: int | None = None
    force_start: bool | None = None
    f_l_piece_prio: bool | None = None
    seq_dl: bool | None = None
    super_seeding: bool | None = None
    priority: int | None = None
    ratio: float | None = None
    ratio_limit: float | None = None
    seeding_time: int | None = None
    seeding_time_limit: int | None = None
    inactive_seeding_time_limit: int | None = None
    num_complete: int | None = None
    num_incomplete: int | None = None
    num_seeds: int | None = None
    num_leechs: int | None = None
    dlspeed: int | None = None
    upspeed: int | None = None
    eta: int | None = None
    availability: float | None = None


class _TorrentTrackerPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    status: int = 0
    tier: int = 0
    num_peers: int | None = None
    num_seeds: int | None = None
    num_leeches: int | None = None
    num_downloaded: int | None = None
    msg: str | None = None


class _TorrentFilePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    name: str
    size: int = 0
    progress: float = 0
    priority: int = 0
    is_seed: bool | None = None
    availability: float | None = None
    piece_range: list[int] | None = None


class _TorrentPropertiesPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    save_path: str | None = None
    creation_date: int | None = None
    piece_size: int | None = None
    comment: str | None = None
    total_wasted: int | None = None
    total_uploaded: int | None = None
    total_uploaded_session: int | None = None
    total_downloaded: int | None = None
    total_downloaded_session: int | None = None
    up_limit: int | None = None
    dl_limit: int | None = None
    time_elapsed: int | None = None
    seeding_time: int | None = None
    nb_connections: int | None = None
    nb_connections_limit: int | None = None
    share_ratio: float | None = None
    addition_date: int | None = None
    completion_date: int | None = None
    created_by: str | None = None
    last_seen: int | None = None
    peers: int | None = None
    peers_total: int | None = None
    seeds: int | None = None
    seeds_total: int | None = None
    total_size: int | None = None
    pieces_num: int | None = None
    pieces_have: int | None = None


class _CategoryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    savePath: str | None = None
    save_path: str | None = None


class _PreferencesPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    save_path: str | None = None


_TORRENT_LIST = TypeAdapter(list[_TorrentPayload])
_TORRENT_TRACKER_LIST = TypeAdapter(list[_TorrentTrackerPayload])
_TORRENT_FILE_LIST = TypeAdapter(list[_TorrentFilePayload])
_CATEGORY_MAP = TypeAdapter(dict[str, _CategoryPayload])


def _validated_json(response: httpx.Response, adapter: TypeAdapter[Any], context: str) -> Any:
    try:
        return adapter.validate_python(response.json())
    except (ValueError, ValidationError) as exc:
        raise QBittorrentError(f"qBittorrent returned malformed {context} data") from exc


@dataclass(frozen=True, slots=True)
class TorrentObservation:
    info_hash: str
    name: str
    progress: float
    state: str
    save_path: str
    content_path: str | None
    category: str
    tags: tuple[str, ...]
    tracker: str | None
    total_size: int | None
    added_at: int | None
    completed_at: int | None
    amount_left: int | None = None
    auto_tmm: bool | None = None
    download_limit: int | None = None
    upload_limit: int | None = None
    downloaded: int | None = None
    uploaded: int | None = None
    force_start: bool | None = None
    first_last_piece_priority: bool | None = None
    sequential_download: bool | None = None
    super_seeding: bool | None = None
    priority: int | None = None
    ratio: float | None = None
    ratio_limit: float | None = None
    seeding_time: int | None = None
    seeding_time_limit: int | None = None
    inactive_seeding_time_limit: int | None = None
    seeds: int | None = None
    leeches: int | None = None
    download_speed: int | None = None
    upload_speed: int | None = None
    eta: int | None = None
    availability: float | None = None

    @property
    def checking(self) -> bool:
        """Whether qBittorrent is verifying payload or resume data."""

        return self.state.casefold().startswith("checking")

    @property
    def complete(self) -> bool:
        return self.progress >= 1 and not self.checking and self.state != "metaDL"


@dataclass(frozen=True, slots=True)
class TorrentCategory:
    name: str
    save_path: str
    resolved_save_path: str


@dataclass(frozen=True, slots=True)
class TorrentTrackerSnapshot:
    url: str
    status: int
    tier: int
    peers: int | None
    seeds: int | None
    leeches: int | None
    downloaded: int | None
    message: str | None

    def as_manifest(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "tier": self.tier,
            "peers": self.peers,
            "seeds": self.seeds,
            "leeches": self.leeches,
            "downloaded": self.downloaded,
            "message": self.message,
        }


class QBittorrentClient:
    """Small async adapter that only exposes explicitly allowed qBit actions."""

    _SAFE_RETRY_METHODS = {"GET", "HEAD"}
    _TRANSIENT_READ_ERRORS = (
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.ConnectError,
    )

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/", timeout=timeout, transport=transport
        )
        self._authenticated = False

    async def __aenter__(self) -> QBittorrentClient:
        await self.login()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        # Do not leave the authentication socket in the connection pool. Some
        # qBittorrent/proxy combinations close the connection immediately after
        # the login response (especially the 204 response used by qBittorrent
        # 5.2+). Keeping that socket eligible for reuse can make the next GET fail
        # with ``Server disconnected without sending a response`` even though
        # authentication succeeded. The cookie jar is independent of the socket,
        # so the SID is still sent on the following fresh connection.
        response = await self._client.post(
            "api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            headers={"Connection": "close"},
        )
        body = response.text.strip()
        # qBittorrent <= 5.1 returns HTTP 200 with ``Ok.`` on a successful
        # login. qBittorrent 5.2+ uses HTTP 204 No Content for the same
        # successful empty response. Accept both wire formats.
        if (response.status_code == 200 and body == "Ok.") or response.status_code == 204:
            self._authenticated = True
            return

        body_lower = body.lower()
        if response.status_code == 403 and "banned" in body_lower:
            raise QBittorrentAuthError(
                "qBittorrent temporarily banned Medialogue's IP after too many failed login attempts. "
                "Wait for the qBittorrent WebUI ban duration (or clear/restart the ban), then test the client again.",
                reason="ip_banned",
            )
        if response.status_code == 200 and body_lower in {"fails.", "fails"}:
            raise QBittorrentAuthError(
                "qBittorrent rejected the configured username/password.",
                reason="credentials_rejected",
            )
        if response.status_code in {401, 403}:
            raise QBittorrentAuthError(
                f"qBittorrent rejected WebAPI authentication (HTTP {response.status_code}).",
                reason="authentication_rejected",
            )

        detail = f"HTTP {response.status_code}"
        if response.status_code == 404:
            detail += "; check that the configured URL points to the qBittorrent WebUI/API base URL"
        raise QBittorrentAuthError(
            f"qBittorrent login returned an unexpected response ({detail}).",
            reason="unexpected_login_response",
        )

    async def health(self) -> dict[str, Any]:
        response = await self._request("GET", "/api/v2/app/version")
        return {"status": "healthy", "version": response.text.strip()}

    async def list_torrents(self) -> list[TorrentObservation]:
        response = await self._request("GET", "/api/v2/torrents/info")
        return [self._observation(item) for item in _validated_json(response, _TORRENT_LIST, "torrent list")]

    async def list_categories(self) -> list[TorrentCategory]:
        """Return qBittorrent categories with their effective destination paths."""

        categories_response, preferences_response = await asyncio.gather(
            self._request("GET", "/api/v2/torrents/categories"),
            self._request("GET", "/api/v2/app/preferences"),
        )
        categories = _validated_json(categories_response, _CATEGORY_MAP, "category list")
        try:
            preferences = _PreferencesPayload.model_validate(preferences_response.json())
        except (ValueError, ValidationError) as exc:
            raise QBittorrentError("qBittorrent returned malformed preferences data") from exc
        default_save_path = preferences.save_path or ""
        rows: list[TorrentCategory] = []
        for key, item in categories.items():
            name = (item.name or key).strip()
            if not name:
                continue
            save_path = (item.savePath or item.save_path or "").strip()
            rows.append(
                TorrentCategory(
                    name=name,
                    save_path=save_path,
                    resolved_save_path=_resolved_category_path(default_save_path, save_path),
                )
            )
        return sorted(rows, key=lambda item: item.name.casefold())

    async def get_torrent(self, info_hash: str) -> TorrentObservation | None:
        response = await self._request(
            "GET", "/api/v2/torrents/info", params={"hashes": info_hash.lower()}
        )
        values = _validated_json(response, _TORRENT_LIST, "torrent details")
        return self._observation(values[0]) if values else None

    async def get_torrent_trackers(self, info_hash: str) -> tuple[TorrentTrackerSnapshot, ...]:
        response = await self._request(
            "GET", "/api/v2/torrents/trackers", params={"hash": info_hash.lower()}
        )
        values = _validated_json(response, _TORRENT_TRACKER_LIST, "torrent tracker list")
        return tuple(
            TorrentTrackerSnapshot(
                url=item.url,
                status=item.status,
                tier=item.tier,
                peers=item.num_peers,
                seeds=item.num_seeds,
                leeches=item.num_leeches,
                downloaded=item.num_downloaded,
                message=item.msg,
            )
            for item in values
        )

    async def get_torrent_recovery_snapshot(self, info_hash: str) -> dict[str, Any]:
        """Return portable qBittorrent state needed to recreate a torrent.

        qBittorrent's private fast-resume files are intentionally excluded.
        The exported .torrent plus these public Web API fields are the durable,
        cross-version recovery contract.
        """

        properties_response, files_response, trackers = await asyncio.gather(
            self._request("GET", "/api/v2/torrents/properties", params={"hash": info_hash.lower()}),
            self._request("GET", "/api/v2/torrents/files", params={"hash": info_hash.lower()}),
            self.get_torrent_trackers(info_hash),
        )
        try:
            properties = _TorrentPropertiesPayload.model_validate(properties_response.json())
        except (ValueError, ValidationError) as exc:
            raise QBittorrentError("qBittorrent returned malformed torrent properties") from exc
        files = _validated_json(files_response, _TORRENT_FILE_LIST, "torrent file list")
        return {
            "properties": properties.model_dump(exclude_none=True),
            "files": [item.model_dump(exclude_none=True) for item in files],
            "trackers": [item.as_manifest() for item in trackers],
        }

    async def export_torrent(self, info_hash: str) -> bytes:
        """Export the current .torrent metadata from qBittorrent.

        qBittorrent exposes raw torrent bytes from ``/api/v2/torrents/export``.
        This is used only for the recovery archive and never changes torrent
        state or downloaded media.
        """

        response = await self._request(
            "GET", "/api/v2/torrents/export", params={"hash": info_hash.lower()}
        )
        payload = response.content
        if not payload:
            raise QBittorrentError("qBittorrent returned an empty torrent export")
        return payload

    async def add_torrent(
        self,
        torrent: bytes,
        *,
        filename: str = "download.torrent",
        save_path: str | None = None,
        category: str | None = None,
        auto_tmm: bool | None = None,
        tags: tuple[str, ...] = (),
        sequential_order: bool = False,
        first_last_first: bool = False,
        content_layout: str = "default",
        add_to_top: bool = False,
    ) -> None:
        data: dict[str, str] = {}
        if save_path:
            data["savepath"] = save_path
        if category:
            data["category"] = category
        if auto_tmm is not None:
            data["autoTMM"] = str(auto_tmm).lower()
        if tags:
            data["tags"] = ",".join(tags)
        if sequential_order:
            data["sequentialDownload"] = "true"
        if first_last_first:
            data["firstLastPiecePrio"] = "true"
        if content_layout in {"original", "subfolder"}:
            data["contentLayout"] = content_layout.title()
        response = await self._request(
            "POST",
            "/api/v2/torrents/add",
            data=data,
            files={"torrents": (filename, torrent, "application/x-bittorrent")},
        )
        if response.text.strip() not in {"", "Ok."}:
            raise QBittorrentError("qBittorrent rejected the torrent")
        if add_to_top:
            info_hash = _torrent_info_hash(torrent)
            if info_hash:
                await self._move_to_top_when_visible(info_hash)

    async def add_url(
        self,
        url: str,
        *,
        save_path: str | None = None,
        category: str | None = None,
        auto_tmm: bool | None = None,
        tags: tuple[str, ...] = (),
        sequential_order: bool = False,
        first_last_first: bool = False,
        content_layout: str = "default",
        add_to_top: bool = False,
    ) -> None:
        data: dict[str, str] = {"urls": url}
        if save_path:
            data["savepath"] = save_path
        if category:
            data["category"] = category
        if auto_tmm is not None:
            data["autoTMM"] = str(auto_tmm).lower()
        if tags:
            data["tags"] = ",".join(tags)
        if sequential_order:
            data["sequentialDownload"] = "true"
        if first_last_first:
            data["firstLastPiecePrio"] = "true"
        if content_layout in {"original", "subfolder"}:
            data["contentLayout"] = content_layout.title()
        response = await self._request("POST", "/api/v2/torrents/add", data=data)
        if response.text.strip() not in {"", "Ok."}:
            raise QBittorrentError("qBittorrent rejected the torrent URL")
        if add_to_top:
            info_hash = _magnet_info_hash(url)
            if info_hash:
                await self._move_to_top_when_visible(info_hash)

    async def _move_to_top_when_visible(self, info_hash: str) -> None:
        for attempt in range(8):
            if await self.get_torrent(info_hash) is not None:
                await self._request("POST", "/api/v2/torrents/topPrio", data={"hashes": info_hash.lower()})
                return
            if attempt < 7:
                await asyncio.sleep(0.25)

    async def remove_torrent(self, info_hash: str, *, delete_files: bool = False) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": info_hash.lower(), "deleteFiles": str(delete_files).lower()},
        )

    async def _request_once_with_safe_retry(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        method_upper = method.upper()
        request_path = path.lstrip("/")
        try:
            return await self._client.request(method_upper, request_path, **kwargs)
        except self._TRANSIENT_READ_ERRORS as exc:
            if method_upper not in self._SAFE_RETRY_METHODS:
                raise QBittorrentError(
                    f"qBittorrent connection failed during {method_upper}: {exc}"
                ) from exc
            try:
                return await self._client.request(method_upper, request_path, **kwargs)
            except self._TRANSIENT_READ_ERRORS as retry_exc:
                raise QBittorrentError(
                    f"qBittorrent connection dropped twice while reading {path}: {retry_exc}"
                ) from retry_exc

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not self._authenticated:
            await self.login()

        method_upper = method.upper()
        response = await self._request_once_with_safe_retry(method_upper, path, **kwargs)
        if response.status_code in {401, 403}:
            self._authenticated = False
            await self.login()
            response = await self._request_once_with_safe_retry(method_upper, path, **kwargs)
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise QBittorrentError(f"qBittorrent request failed: {response.status_code}") from exc
        return response

    @staticmethod
    def _observation(item: _TorrentPayload) -> TorrentObservation:
        return TorrentObservation(
            info_hash=item.hash.lower(),
            name=item.name or "",
            progress=item.progress or 0,
            state=item.state or "unknown",
            save_path=item.save_path or "",
            content_path=item.content_path,
            category=item.category or "",
            tags=tuple(tag.strip() for tag in (item.tags or "").split(",") if tag.strip()),
            tracker=item.tracker or None,
            total_size=item.total_size,
            added_at=item.added_on,
            completed_at=item.completion_on,
            amount_left=item.amount_left,
            auto_tmm=item.auto_tmm,
            download_limit=item.dl_limit,
            upload_limit=item.up_limit,
            downloaded=item.downloaded,
            uploaded=item.uploaded,
            force_start=item.force_start,
            first_last_piece_priority=item.f_l_piece_prio,
            sequential_download=item.seq_dl,
            super_seeding=item.super_seeding,
            priority=item.priority,
            ratio=item.ratio,
            ratio_limit=item.ratio_limit,
            seeding_time=item.seeding_time,
            seeding_time_limit=item.seeding_time_limit,
            inactive_seeding_time_limit=item.inactive_seeding_time_limit,
            seeds=item.num_seeds if item.num_seeds is not None else item.num_complete,
            leeches=item.num_leechs if item.num_leechs is not None else item.num_incomplete,
            download_speed=item.dlspeed,
            upload_speed=item.upspeed,
            eta=item.eta,
            availability=item.availability,
        )
