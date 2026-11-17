"""Change match per release: a release moves to the movie it really is.

The movie it leaves goes away without a trace when a scan made it from those
files, and the user is asked when anything says it was wanted for itself.
"""

import asyncio
import os
from uuid import UUID

from sqlalchemy import func, select

from app.api import tmdb as tmdb_api
from app.db import session as db_session
from app.integrations.tmdb import TMDBMovieMatch
from app.models.domain import Event, MediaDirectory, Movie, MovieRelease
from tests.test_libraries import client, create_root, login  # noqa: F401  (client is a fixture)


class FakeTMDB:
    def __init__(self, api_key: str) -> None:
        pass

    async def get_movie(self, tmdb_id: int) -> TMDBMovieMatch:
        return TMDBMovieMatch(tmdb_id=tmdb_id, title={679: "Aliens", 348: "Alien"}.get(tmdb_id, f"Movie {tmdb_id}"),
                              original_title=None, year=1986 if tmdb_id == 679 else 1979, overview=None, poster_path=None)

    async def close(self) -> None:
        pass


def setup(client):
    headers = login(client)
    assert client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "key", "enabled": True}).status_code == 200
    client.app.dependency_overrides[tmdb_api.get_tmdb_client_factory] = lambda: FakeTMDB
    return headers


def movie_with_releases(root_id: str, path: str, title: str, tmdb_id: int | None, names: list[str], *, source: str | None = None):
    async def run():
        async with db_session.async_session_factory() as db:
            movie = Movie(title=title, sort_title=title.lower(), tmdb_id=tmdb_id)
            db.add(movie)
            await db.flush()
            ids = []
            for name in names:
                release = MovieRelease(movie_id=movie.id, raw_release_name=name)
                db.add(release)
                await db.flush()
                db.add(MediaDirectory(storage_root_id=UUID(root_id), resolved_path=os.path.join(path, name), movie_release_id=release.id))
                ids.append(release.id)
            if source:
                db.add(Event(event_type="movie.added", entity_type="movie", entity_id=movie.id, message="Added.",
                             details={"source": source}))
            await db.commit()
            return movie.id, ids

    return asyncio.run(run())


def state(movie_id):
    async def run():
        async with db_session.async_session_factory() as db:
            movie = await db.get(Movie, movie_id)
            events = await db.scalar(select(func.count()).select_from(Event).where(Event.entity_id == movie_id))
            return movie, events

    return asyncio.run(run())


def release_movie(release_id):
    async def run():
        async with db_session.async_session_factory() as db:
            return (await db.get(MovieRelease, release_id)).movie_id

    return asyncio.run(run())


def test_a_release_moves_to_a_new_movie_and_a_scan_made_movie_is_removed_without_history(client, tmp_path):
    headers = setup(client)
    root = create_root(client, headers, tmp_path / "m", "M")
    wrong, [release] = movie_with_releases(root["id"], str(tmp_path / "m"), "Alien", 348, ["Aliens.1986.1080p"])

    moved = client.post(f"/api/v1/movies/{wrong}/releases/rematch", headers=headers,
                        json={"release_ids": [str(release)], "tmdb_id": 679})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["created"] is True and body["source_removed"] is True and body["title"] == "Aliens"
    assert str(release_movie(release)) == body["movie_id"]
    movie, events = state(wrong)
    assert movie is None and events == 0
    client.app.dependency_overrides.clear()


def test_a_release_joins_a_movie_already_in_the_library_and_the_other_releases_stay(client, tmp_path):
    headers = setup(client)
    root = create_root(client, headers, tmp_path / "m", "M")
    right, _ = movie_with_releases(root["id"], str(tmp_path / "m"), "Aliens", 679, ["Aliens.1986.2160p"])
    wrong, [stays, goes] = movie_with_releases(root["id"], str(tmp_path / "m"), "Alien", 348,
                                               ["Alien.1979.1080p", "Aliens.1986.1080p"])

    moved = client.post(f"/api/v1/movies/{wrong}/releases/rematch", headers=headers,
                        json={"release_ids": [str(goes)], "tmdb_id": 679})
    assert moved.status_code == 200, moved.text
    assert moved.json() == {"movie_id": str(right), "title": "Aliens", "year": None, "created": False, "source_removed": False}
    assert release_movie(goes) == right and release_movie(stays) == wrong
    client.app.dependency_overrides.clear()


def test_a_movie_added_by_hand_is_only_removed_when_the_user_says_so(client, tmp_path):
    headers = setup(client)
    root = create_root(client, headers, tmp_path / "m", "M")
    wrong, [release] = movie_with_releases(root["id"], str(tmp_path / "m"), "Alien", 348, ["Aliens.1986.1080p"], source="manual_add")
    payload = {"release_ids": [str(release)], "tmdb_id": 679}

    asked = client.post(f"/api/v1/movies/{wrong}/releases/rematch", headers=headers, json=payload)
    assert asked.status_code == 409, asked.text
    error = asked.json()["error"]
    assert error["code"] == "REMATCH_KEEP_OR_REMOVE"
    assert error["details"]["reasons"] == ["It was added to the library by hand."]
    assert release_movie(release) == wrong

    kept = client.post(f"/api/v1/movies/{wrong}/releases/rematch", headers=headers, json={**payload, "remove_emptied": False})
    assert kept.status_code == 200, kept.text
    assert kept.json()["source_removed"] is False
    movie, _ = state(wrong)
    assert movie is not None
    client.app.dependency_overrides.clear()


def test_a_release_cannot_move_to_the_title_it_already_has(client, tmp_path):
    headers = setup(client)
    root = create_root(client, headers, tmp_path / "m", "M")
    movie, [release] = movie_with_releases(root["id"], str(tmp_path / "m"), "Alien", 348, ["Alien.1979.1080p"])

    same = client.post(f"/api/v1/movies/{movie}/releases/rematch", headers=headers,
                       json={"release_ids": [str(release)], "tmdb_id": 348})
    assert same.status_code == 409
    assert same.json()["error"]["code"] == "REMATCH_SAME_TITLE"
    client.app.dependency_overrides.clear()
