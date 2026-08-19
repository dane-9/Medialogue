from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts"}


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
            if not entry.is_dir():
                continue
            try:
                observations.append(self.inspect_directory(entry, base))
            except PermissionError:
                # A symlink escaping the configured root is never followed.
                continue
        return observations

    def inspect_directory(self, directory: Path, allowed_root: Path) -> DirectoryObservation:
        resolved = directory.resolve(strict=True)
        try:
            resolved.relative_to(allowed_root)
        except ValueError as exc:
            raise PermissionError("directory is outside the configured storage root") from exc

        media: list[str] = []
        has_dvd = False
        has_bluray = False
        for entry in resolved.rglob("*"):
            relative = entry.relative_to(resolved)
            upper_parts = {part.upper() for part in relative.parts}
            has_dvd = has_dvd or "VIDEO_TS" in upper_parts
            has_bluray = has_bluray or "BDMV" in upper_parts
            if entry.is_file() and entry.suffix.casefold() in VIDEO_SUFFIXES:
                media.append(relative.as_posix())
        return DirectoryObservation(
            path=str(resolved),
            name=resolved.name,
            media_files=tuple(sorted(media)),
            has_dvd_structure=has_dvd,
            has_bluray_structure=has_bluray,
        )
