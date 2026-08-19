"""Optional PostgreSQL-only regression checks.

The normal local suite uses SQLite for speed. CI supplies
``MEDIALOGUE_TEST_POSTGRES_URL`` after applying the Alembic history so this file
also exercises behavior that SQLite cannot accurately model (asyncpg, JSONB,
and PostgreSQL uniqueness/transaction semantics).
"""

from __future__ import annotations

import os
import random

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.domain import IdentityState, Movie, MovieRelease, ReleaseState


POSTGRES_URL = os.getenv("MEDIALOGUE_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="MEDIALOGUE_TEST_POSTGRES_URL is not configured")


@pytest.mark.asyncio
async def test_postgres_transaction_rollback_unique_tmdb_and_jsonb_roundtrip() -> None:
    assert POSTGRES_URL is not None
    engine = create_async_engine(POSTGRES_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tmdb_id = random.randint(800_000_000, 899_999_999)
    movie_id = None
    try:
        async with sessions() as db:
            movie = Movie(
                title="Part 19 PostgreSQL",
                sort_title="part 19 postgresql",
                year=2026,
                tmdb_id=tmdb_id,
                identity_state=IdentityState.MATCHED,
            )
            db.add(movie)
            await db.commit()
            movie_id = movie.id

            duplicate = Movie(
                title="Duplicate TMDB Identity",
                sort_title="duplicate tmdb identity",
                year=2026,
                tmdb_id=tmdb_id,
                identity_state=IdentityState.MATCHED,
            )
            db.add(duplicate)
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

            assert await db.scalar(select(func.count()).select_from(Movie).where(Movie.tmdb_id == tmdb_id)) == 1

            release = MovieRelease(
                movie_id=movie_id,
                raw_release_name="Part 19 PostgreSQL 2026 2160p WEB-DL-GROUP",
                release_state=ReleaseState.CURRENT,
                parse_snapshot={
                    "parser_version": 1,
                    "nested": {"hdr": ["DV", "HDR10"], "hybrid": True},
                },
            )
            db.add(release)
            await db.commit()
            release_id = release.id

        async with sessions() as db:
            loaded = await db.get(MovieRelease, release_id)
            assert loaded is not None
            assert loaded.parse_snapshot["nested"]["hdr"] == ["DV", "HDR10"]
            assert loaded.parse_snapshot["nested"]["hybrid"] is True
            movie = await db.get(Movie, movie_id)
            await db.delete(loaded)
            if movie is not None:
                await db.delete(movie)
            await db.commit()
    finally:
        await engine.dispose()
