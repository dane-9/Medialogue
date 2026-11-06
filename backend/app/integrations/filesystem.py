from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts"}


class OutsideMediaRootError(PermissionError):
    """An intentionally excluded path, not a failed filesystem read."""


def _raise_walk_error(error: OSError) -> None:
    raise error


def _dangling_symlink(path: Path) -> bool:
    if not path.is_symlink():
        return False
    try:
        path.stat()
    except FileNotFoundError:
        return True
    return False


@dataclass(frozen=True, slots=True)
class DirectoryObservation:
    path: str
    name: str
    media_files: tuple[str, ...]
    has_dvd_structure: bool
    has_bluray_structure: bool


class FilesystemObserver:
    """Enumerates explicitly supplied roots without mutating their contents."""

    def scan_root(self, root: str) -> list[DirectoryObservation]:
        base = Path(root).resolve(strict=True)
        if not base.is_dir():
            raise NotADirectoryError(root)
        observations: list[DirectoryObservation] = []
        for entry in sorted(base.iterdir()):
            if _dangling_symlink(entry):
                # A broken link is not unreadable media; skipping it keeps
                # one stale link from failing the whole folder scan.
                continue
            if not stat.S_ISDIR(entry.stat().st_mode):
                continue
            try:
                observations.append(self.inspect_directory(entry, base))
            except OutsideMediaRootError:
                # A symlink escaping the configured root is never followed.
                continue
        return observations

    def inspect_directory(self, directory: Path, allowed_root: Path) -> DirectoryObservation:
        resolved = directory.resolve(strict=True)
        try:
            resolved.relative_to(allowed_root)
        except ValueError as exc:
            raise OutsideMediaRootError("directory is outside the configured media folder") from exc

        media: list[str] = []
        has_dvd = False
        has_bluray = False
        # pathlib glob can suppress traversal errors. An incomplete inventory
        # must fail the scan before reconciliation marks anything missing.
        for parent, directories, files in os.walk(resolved, onerror=_raise_walk_error, followlinks=False):
            file_names = set(files)
            for name in directories + files:
                entry = Path(parent) / name
                relative = entry.relative_to(resolved)
                upper_parts = {part.upper() for part in relative.parts}
                has_dvd = has_dvd or "VIDEO_TS" in upper_parts
                has_bluray = has_bluray or "BDMV" in upper_parts
                if name in file_names and _dangling_symlink(entry):
                    continue
                if name in file_names and stat.S_ISREG(entry.stat().st_mode) and entry.suffix.casefold() in VIDEO_SUFFIXES:
                    media.append(relative.as_posix())
        return DirectoryObservation(
            path=str(resolved),
            name=resolved.name,
            media_files=tuple(sorted(media)),
            has_dvd_structure=has_dvd,
            has_bluray_structure=has_bluray,
        )
