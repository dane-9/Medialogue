"""Folders that scans pass over, because their title was removed with its files kept."""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import IgnoredFolder


def path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def path_inside(path: str, folder: str) -> bool:
    """`path` is `folder` or lies inside it."""

    inner, outer = path_key(path), path_key(folder)
    return inner == outer or inner.startswith(outer.rstrip("\\/") + os.sep)


async def folder_ignored(db: AsyncSession, path: str) -> bool:
    """Whether a scan should leave this folder alone."""

    folders = (await db.scalars(select(IgnoredFolder.resolved_path))).all()
    return any(path_inside(path, folder) for folder in folders)
