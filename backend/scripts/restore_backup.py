"""Decrypt and validate a backup into a new staging directory; never overwrite a live install."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.backup_crypto import decrypt_backup


def stage_restore(source: Path, destination: Path, recovery_key: str | None = None) -> dict:
    if destination.exists():
        raise ValueError("Choose a new, empty destination path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".medialogue-restore-", dir=destination.parent) as scratch:
        work = Path(scratch)
        archive = source
        if recovery_key:
            archive = work / "decrypted.zip"
            decrypt_backup(source, archive, recovery_key)
        extracted = work / "staged"
        extracted.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            seen = set()
            total = 0
            for entry in bundle.infolist():
                name = entry.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if (path.is_absolute() or ".." in path.parts or ":" in name or name.casefold() in seen
                        or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("Unsafe or duplicate path in backup")
                reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
                if any(part.endswith((".", " ")) or part.split(".")[0].upper() in reserved for part in path.parts):
                    raise ValueError("Unsafe platform-specific path in backup")
                seen.add(name.casefold())
                total += entry.file_size
                if total > 100 * 1024 ** 3:
                    raise ValueError("Backup exceeds the 100 GiB restore staging limit")
                target = extracted.joinpath(*path.parts)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
        metadata = json.loads((extracted / "backup-metadata.json").read_text())
        if metadata.get("recovery_schema_version") != 1:
            raise ValueError("Unsupported recovery bundle version")
        for name, expected in metadata.get("checksums", {}).items():
            relative = PurePosixPath(name.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or ":" in name:
                raise ValueError("Unsafe checksum inventory path")
            with extracted.joinpath(*relative.parts).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError(f"Backup integrity check failed: {name}")
        inventory = json.loads((extracted / "inventory/torrent-archive-inventory.json").read_text())
        for item in inventory["files"]:
            relative = PurePosixPath(item["bundle_path"].replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or ":" in str(relative):
                raise ValueError("Unsafe archive inventory path")
            path = extracted.joinpath(*relative.parts)
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != item["sha256"] or path.stat().st_size != item["size"]:
                raise ValueError(f"Archive integrity check failed: {relative}")
        database = extracted / "database/medialogue.db"
        if not database.is_file():
            raise ValueError("Database backup is missing")
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database integrity check failed")
        finally:
            connection.close()
        extracted.rename(destination)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--encrypted", action="store_true")
    args = parser.parse_args()
    key = getpass.getpass("Recovery key: ") if args.encrypted or args.backup.suffix == ".mlbackup" else None
    stage_restore(args.backup.resolve(), args.destination.resolve(), key)
    print(f"Verified recovery files staged in {args.destination}.")
    print("Stop Medialogue before restoring database/medialogue.db and config/live files to /config.")
    print("Restore torrent-archive/torrents and manifests to /torrent-archive/torrents and /torrent-archive/manifests.")
    print("Retain the original application secret from config/application-config-export.json.")
    print("Start Medialogue, check paths and clients, then inspect the bulk torrent re-import plan.")


if __name__ == "__main__":
    main()
