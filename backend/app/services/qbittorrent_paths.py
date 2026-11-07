"""Mapping qBittorrent's save paths onto the paths Medialogue sees."""

from __future__ import annotations

from pathlib import PurePosixPath
from uuid import UUID

from app.models.domain import RemotePathMapping


def _comparison_path(value: str | None) -> str:
    """Normalize path separators for prefix comparisons without touching FS."""

    return _normalized_path(value).casefold()


def _normalized_path(value: str | None) -> str:
    """Normalize separators while preserving the path's original casing.

    qBittorrent paths are compared case-insensitively because the remote
    client may be Windows, but the mapped local path can be a case-sensitive
    Linux filesystem.  Never derive the local suffix from a case-folded path.
    """

    if not value:
        return ""
    normalized = value.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized


def _inside_local_root(path: str | None, root: str) -> bool:
    """Check a resolved *local* path against a configured media folder.

    Remote qBittorrent prefixes are matched case-insensitively, but after
    mapping Medialogue is operating in a Linux container.  The local mount
    namespace is case-sensitive.  Treating ``/Movies/...`` as if it were under
    the local root ``/movies`` lets an unmatched remote path masquerade as a
    valid local path and creates false TORRENT_PATH_NOT_FOUND Problems.
    """

    if not path or not root:
        return False
    try:
        # This is a path in the container's mount namespace, even when the
        # check happens on a Windows development host.  PurePosixPath keeps
        # Linux's case-sensitive component comparison without touching the
        # host filesystem.
        PurePosixPath(_normalized_path(path)).relative_to(PurePosixPath(_normalized_path(root)))
        return True
    except ValueError:
        return False


def _join_prefix(local_prefix: str, suffix: str) -> str:
    local = local_prefix.replace("\\", "/").rstrip("/")
    if not suffix:
        return local
    return f"{local}/{suffix.lstrip('/')}"


def resolve_remote_path(
    reported_path: str | None,
    mappings: list[RemotePathMapping],
) -> tuple[str | None, UUID | None]:
    """Translate a qBit path while retaining the original path separately."""

    if not reported_path:
        return None, None
    reported_clean = _normalized_path(reported_path)
    reported_normalized = reported_clean.casefold()
    candidates = sorted(
        mappings,
        key=lambda mapping: len(_comparison_path(mapping.remote_prefix)),
        reverse=True,
    )
    for mapping in candidates:
        remote_clean = _normalized_path(mapping.remote_prefix)
        remote_prefix = remote_clean.casefold()
        if reported_normalized == remote_prefix or reported_normalized.startswith(remote_prefix + "/"):
            suffix = reported_clean[len(remote_clean) :].lstrip("/")
            return _join_prefix(mapping.local_prefix, suffix), mapping.id
    return reported_path, None
