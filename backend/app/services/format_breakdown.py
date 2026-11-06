"""The Custom Formats behind a comparison's two scores, for the score popup.

Upgrade and Replace Media both compare a current release with a candidate, and
both store the evidence the same way in ``comparison_snapshot``: the current
release's rows under ``current.score_snapshot.breakdown`` and the candidate's
under ``candidate.formats``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import CustomFormat
from app.services.builtin_formats import format_group


def _matched_rows(rows: Any) -> list[dict]:
    return [item for item in (rows if isinstance(rows, list) else []) if isinstance(item, dict) and item.get("matched")]


async def _format_details(db: AsyncSession, *row_sets: list[dict]) -> dict[str, tuple[str, str | None]]:
    """The current name and group of every format in the given score rows, by id.

    Score rows freeze the name a format had when it was scored; a format renamed
    since, such as a built-in, should read as it is called now.
    """

    ids: set[UUID] = set()
    for rows in row_sets:
        for item in rows:
            try:
                ids.add(UUID(str(item.get("custom_format_id"))))
            except ValueError:
                continue
    if not ids:
        return {}
    formats = (await db.execute(
        select(CustomFormat.id, CustomFormat.name, CustomFormat.builtin_key, CustomFormat.condition_definition)
        .where(CustomFormat.id.in_(ids))
    )).all()
    return {str(id_): (name, format_group(key, definition)) for id_, name, key, definition in formats}


def _matched_formats(rows: list[dict], details: dict[str, tuple[str, str | None]]) -> list[dict[str, Any]]:
    """Formats that matched, ordered by impact then name.

    A zero contribution is kept: a matched format that scores nothing is still
    a fact about the release, and hiding it makes the score look unexplained.
    """

    matched = []
    for item in rows:
        name, group = details.get(str(item.get("custom_format_id") or ""), (None, None))
        matched.append({
            "custom_format_id": str(item.get("custom_format_id") or ""),
            "custom_format_name": name or str(item.get("custom_format_name") or "Custom Format"),
            "contribution": int(item.get("contribution") or 0),
            # A group frozen into the row wins; otherwise the format's own.
            "group": item.get("custom_format_group") or group,
        })
    return sorted(matched, key=lambda item: (-item["contribution"], item["custom_format_name"].casefold()))


async def comparison_formats(db: AsyncSession, snapshot: dict[str, Any] | None) -> tuple[list[dict], list[dict]]:
    """(current release's matched formats, candidate's matched formats)."""

    snapshot = snapshot or {}
    current = _matched_rows(((snapshot.get("current") or {}).get("score_snapshot") or {}).get("breakdown"))
    candidate = _matched_rows((snapshot.get("candidate") or {}).get("formats"))
    details = await _format_details(db, current, candidate)
    return _matched_formats(current, details), _matched_formats(candidate, details)
