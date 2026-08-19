from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class RemotePathMapping:
    mapping_id: str
    remote_prefix: str
    local_prefix: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    reported_path: str
    resolved_path: str | None
    mapping_id: str | None
    failure_reason: str | None = None


def resolve_reported_path(reported_path: str, mappings: list[RemotePathMapping]) -> ResolvedPath:
    """Translate on path-component boundaries and retain original evidence."""
    reported = PurePosixPath(reported_path)
    candidates: list[tuple[int, RemotePathMapping, PurePosixPath]] = []
    for mapping in mappings:
        if not mapping.enabled:
            continue
        remote = PurePosixPath(mapping.remote_prefix)
        try:
            relative = reported.relative_to(remote)
        except ValueError:
            continue
        candidates.append((len(remote.parts), mapping, relative))

    if not candidates:
        return ResolvedPath(reported_path, None, None, "PATH_MAPPING_FAILED")

    _, mapping, relative = max(candidates, key=lambda item: item[0])
    resolved = PurePosixPath(mapping.local_prefix).joinpath(relative)
    return ResolvedPath(reported_path, str(resolved), mapping.mapping_id)


def is_inside_root(path: str, root: str) -> bool:
    try:
        PurePosixPath(path).relative_to(PurePosixPath(root))
        return True
    except ValueError:
        return False
