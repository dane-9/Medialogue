"""Restore the torrent archive from a backup ZIP uploaded by hand.

The ZIP is the "Medialogue Backups" folder downloaded from Google Drive (Drive
zips a folder on download, and splits a large one into several ZIPs; each part
can be uploaded on its own). Only ``.torrent`` files under a ``torrents``
folder and ``.json`` manifests under a ``manifests`` folder are used; they are
written to the archive by their info hash, never by a path from the ZIP.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.torrent_archive import _atomic_write, _valid_torrent_payload, archive_paths

#: Larger members are not torrents or manifests.
MEMBER_LIMIT = 64 * 1024 * 1024
#: A BitTorrent info hash: v1 (SHA-1) or v2 (SHA-256), in hex.
INFO_HASH = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _kind(name: PurePosixPath) -> str | None:
    if ".." in name.parts:
        return None
    folders = {part.casefold() for part in name.parts[:-1]}
    if name.suffix == ".torrent" and "torrents" in folders:
        return "torrents"
    if name.suffix == ".json" and "manifests" in folders:
        return "manifests"
    return None


def import_backup_zip(path: Path) -> dict[str, Any]:
    """Write the ZIP's torrents and manifests into the archive.

    Files already present and identical are left alone, so uploading the same
    ZIP again changes nothing.
    """

    counts = {"written": 0, "present": 0, "skipped": 0}
    errors: list[str] = []
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError("That file is not a ZIP. Upload the backup folder as downloaded from Google Drive.") from exc
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename.replace("\\", "/"))
            kind = _kind(name)
            if kind is None:
                continue
            info_hash = name.stem.lower()
            if not INFO_HASH.fullmatch(info_hash):
                counts["skipped"] += 1
                continue
            if info.file_size > MEMBER_LIMIT:
                counts["skipped"] += 1
                errors.append(f"{name.name}: too large to be a {kind[:-1]}")
                continue
            data = archive.read(info)
            try:
                if kind == "torrents":
                    if not _valid_torrent_payload(data):
                        raise ValueError("not a valid .torrent file")
                else:
                    manifest = json.loads(data)
                    if str(manifest.get("torrent_info_hash") or "").lower() != info_hash:
                        raise ValueError("manifest belongs to a different torrent")
            except (ValueError, AttributeError) as exc:
                counts["skipped"] += 1
                if len(errors) < 20:
                    errors.append(f"{name.name}: {exc}")
                continue
            torrent_path, manifest_path = archive_paths(info_hash)
            target = torrent_path if kind == "torrents" else manifest_path
            if target.is_file() and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(data).digest():
                counts["present"] += 1
                continue
            _atomic_write(target, data)
            counts["written"] += 1
    if not counts["written"] and not counts["present"]:
        raise ValueError("No torrents or manifests were found in that ZIP. Upload the backup folder downloaded from Google Drive.")
    return {**counts, "errors": errors}
