from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class QBittorrentError(RuntimeError):
    pass


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

    @property
    def complete(self) -> bool:
        return self.progress >= 1 and self.state not in {"checkingDL", "metaDL"}


class QBittorrentClient:
    """Small async adapter that only exposes explicitly allowed qBit actions."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
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
        response = await self._client.post(
            "/api/v2/auth/login", data={"username": self.username, "password": self.password}
        )
        if response.status_code != 200 or response.text.strip() != "Ok.":
            raise QBittorrentError("qBittorrent authentication failed")
        self._authenticated = True

    async def health(self) -> dict[str, Any]:
        response = await self._request("GET", "/api/v2/app/version")
        return {"status": "healthy", "version": response.text.strip()}

    async def list_torrents(self) -> list[TorrentObservation]:
        response = await self._request("GET", "/api/v2/torrents/info")
        return [self._observation(item) for item in response.json()]

    async def get_torrent(self, info_hash: str) -> TorrentObservation | None:
        response = await self._request(
            "GET", "/api/v2/torrents/info", params={"hashes": info_hash.lower()}
        )
        values = response.json()
        return self._observation(values[0]) if values else None

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
        tags: tuple[str, ...] = (),
    ) -> None:
        data: dict[str, str] = {}
        if save_path:
            data["savepath"] = save_path
        if category:
            data["category"] = category
        if tags:
            data["tags"] = ",".join(tags)
        response = await self._request(
            "POST",
            "/api/v2/torrents/add",
            data=data,
            files={"torrents": (filename, torrent, "application/x-bittorrent")},
        )
        if response.text.strip() not in {"", "Ok."}:
            raise QBittorrentError("qBittorrent rejected the torrent")

    async def add_url(
        self,
        url: str,
        *,
        save_path: str | None = None,
        category: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> None:
        data: dict[str, str] = {"urls": url}
        if save_path:
            data["savepath"] = save_path
        if category:
            data["category"] = category
        if tags:
            data["tags"] = ",".join(tags)
        response = await self._request("POST", "/api/v2/torrents/add", data=data)
        if response.text.strip() not in {"", "Ok."}:
            raise QBittorrentError("qBittorrent rejected the torrent URL")

    async def remove_torrent(self, info_hash: str, *, delete_files: bool = False) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": info_hash.lower(), "deleteFiles": str(delete_files).lower()},
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not self._authenticated:
            await self.login()
        response = await self._client.request(method, path, **kwargs)
        if response.status_code in {401, 403}:
            self._authenticated = False
            await self.login()
            response = await self._client.request(method, path, **kwargs)
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise QBittorrentError(f"qBittorrent request failed: {response.status_code}") from exc
        return response

    @staticmethod
    def _observation(item: dict[str, Any]) -> TorrentObservation:
        raw_tags = item.get("tags") or ""
        return TorrentObservation(
            info_hash=str(item["hash"]).lower(),
            name=str(item.get("name") or ""),
            progress=float(item.get("progress") or 0),
            state=str(item.get("state") or "unknown"),
            save_path=str(item.get("save_path") or ""),
            content_path=item.get("content_path"),
            category=str(item.get("category") or ""),
            tags=tuple(tag.strip() for tag in raw_tags.split(",") if tag.strip()),
            tracker=item.get("tracker") or None,
            total_size=item.get("total_size"),
            added_at=item.get("added_on"),
            completed_at=item.get("completion_on"),
        )
