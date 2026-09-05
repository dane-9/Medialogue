from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import indexers
from app.api.quality_profiles import _save_settings
from app.core.errors import AppError
from app.db.base import Base
from app.models.domain import MediaProfileOverride, MediaType, Movie, MovieRelease, QualityDefinition, QualityProfile
from app.schemas.quality_profiles import MediaProfileSettingsUpdate
from app.services.quality_profiles import load_effective_profile
from app.services.upgrade_candidates import _movie_targets


@pytest.mark.parametrize("scope,client_scope,valid", [
    ("shows", "movies", False), ("movies", "shows", False),
    ("shows", "shows", True), ("movies", "movies", True),
    ("both", "movies", True), ("both", "shows", True),
])
def test_indexer_client_scope(monkeypatch, scope, client_scope, valid):
    client = SimpleNamespace(id=uuid4(), name="Client", scope=client_scope)
    monkeypatch.setattr(indexers, "get_integration_config_store", lambda: SimpleNamespace(list_download_clients=lambda: [client]))
    if valid:
        indexers._require_download_clients([client.id], scope)
    else:
        with pytest.raises(AppError):
            indexers._require_download_clients([client.id], scope)


@pytest.mark.parametrize("current_rank,expected_preference", [(10, 1), (30, -0.5)])
async def test_excluded_current_quality_remains_an_upgrade_target(current_rank, expected_preference):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            current = QualityDefinition(name="720p WEB-DL", rank=current_rank)
            allowed = QualityDefinition(name="1080p WEB-DL", rank=20)
            movie = Movie(title="Example", sort_title="Example", tmdb_id=123)
            db.add_all([current, allowed, movie])
            await db.flush()
            profile = QualityProfile(name="Selected", settings={"quality_definition_ids": [str(allowed.id)]})
            db.add(profile)
            await db.flush()
            db.add_all([
                MediaProfileOverride(media_type=MediaType.MOVIES, movie_id=movie.id, quality_profile_id=profile.id),
                MovieRelease(movie_id=movie.id, raw_release_name="Example.2020.720p.WEB-DL-GROUP", quality_definition_id=current.id),
            ])
            await db.commit()
            targets, skipped, _ = await _movie_targets(db)
            assert skipped == 0
            assert len(targets) == 1
            assert targets[0].reference.quality_preference == expected_preference
            # Out-of-profile minimum overrides must fail before saving.
            with pytest.raises(AppError):
                await _save_settings(db, MediaType.MOVIES, movie.id, MediaProfileSettingsUpdate(
                    quality_profile_id=profile.id, minimum_quality_definition_override_id=current.id,
                ))
    finally:
        await engine.dispose()
