from __future__ import annotations

import re

from app.core.errors import AppError

_INVALID_SHOW_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_show_folder_name(value: str) -> str:
    """Return one safe directory component for a qBittorrent Show save path.

    qBittorrent may run on a different OS than Medialogue, so use a conservative
    cross-platform component: no separators, traversal components, control
    characters, or Windows-reserved punctuation. Colons and similar title
    punctuation become a readable `` - `` rather than silently disappearing.
    """

    cleaned = _INVALID_SHOW_FOLDER_CHARS.sub(" - ", value.strip())
    cleaned = " ".join(cleaned.split()).strip(" .")
    cleaned = re.sub(r"(?:\s+-\s+){2,}", " - ", cleaned)
    if not cleaned or cleaned in {".", ".."}:
        raise AppError("SHOW_FOLDER_INVALID", "Enter a valid Show folder name.", status_code=422)
    return cleaned[:240].rstrip(" .")


def show_folder_with_year(base_name: str, year: int | None, include_year: bool) -> str:
    base = sanitize_show_folder_name(base_name)
    if not include_year:
        return base
    if year is None:
        raise AppError(
            "SHOW_YEAR_REQUIRED",
            "This Show has no known premiere year, so the year cannot be appended to its folder name.",
            status_code=409,
        )
    suffix = f" {year}"
    if base.casefold().endswith(suffix.casefold()):
        return base
    room = max(1, 240 - len(suffix))
    return f"{base[:room].rstrip(' .')}{suffix}"


def join_qbit_path(root: str, folder: str) -> str:
    root = root.strip()
    if not root:
        raise AppError(
            "QBITTORRENT_CATEGORY_PATH_REQUIRED",
            "The selected Shows category does not resolve to a download root.",
            status_code=409,
        )
    separator = "\\" if "\\" in root and "/" not in root else "/"
    return f"{root.rstrip('/\\')}{separator}{folder}"
