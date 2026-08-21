from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class QBittorrentError(RuntimeError):
    pass


class QBittorrentAuthError(QBittorrentError):
    """Authentication failure with enough detail for safe retry decisions."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


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
        # Keep a trailing slash and use relative API paths so a configured
        # reverse-proxy prefix (for example https://host/qbit/) is preserved.
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
            # Read-only WebAPI calls are safe to repeat. HTTPX evicts a broken
            # connection after these transport errors, so this second attempt
            # naturally uses another socket. One retry is enough to absorb a
            # stale keep-alive connection without masking an actual outage.
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
