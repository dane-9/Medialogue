"""Saves that collide on SQLite's single write lock wait, retry, or stay short."""

import asyncio
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import jobs as jobs_module
from app.services import quality_profiles as profiles_module
from app.services.jobs import with_lock_retries
from app.services.qbittorrent import _claim_write_lock


def _locked() -> OperationalError:
    return OperationalError("UPDATE jobs", {}, sqlite3.OperationalError("database is locked"))


async def test_a_claimed_transaction_cannot_be_overtaken_between_its_read_and_its_write(tmp_path):
    path = tmp_path / "lock.db"
    setup = sqlite3.connect(path, isolation_level=None)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute("CREATE TABLE torrents (id INTEGER PRIMARY KEY, name TEXT)")
    setup.execute("INSERT INTO torrents VALUES (1, 'a')")
    other = sqlite3.connect(path, isolation_level=None, timeout=0.05)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with AsyncSession(engine) as db:
            async with db.begin_nested():
                await _claim_write_lock(db)
                await db.execute(text("SELECT name FROM torrents"))
                # The other writer has to wait rather than slip in here, which
                # is what made the write below fail outright.
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("UPDATE torrents SET name = 'other'")
                await db.execute(text("UPDATE torrents SET name = 'poll'"))
            await db.commit()
    finally:
        await engine.dispose()
    assert setup.execute("SELECT name FROM torrents").fetchone() == ("poll",)
    assert setup.execute("SELECT count(*) FROM torrents WHERE id = 1").fetchone() == (1,)
    other.close()
    setup.close()


@pytest.fixture
def no_retry_wait(monkeypatch):
    monkeypatch.setattr(jobs_module, "_LOCK_RETRY_SECONDS", 0)


async def test_lock_errors_are_retried_until_the_save_works(no_retry_wait):
    calls = []

    async def work():
        calls.append(1)
        if len(calls) < 3:
            raise _locked()
        return "saved"

    assert await with_lock_retries(work) == "saved"
    assert len(calls) == 3


async def test_retries_give_up_eventually_and_other_errors_are_not_retried(no_retry_wait):
    calls = []

    async def always_locked():
        calls.append(1)
        raise _locked()

    with pytest.raises(OperationalError):
        await with_lock_retries(always_locked)
    assert len(calls) == jobs_module._LOCK_RETRIES

    other = []

    async def different():
        other.append(1)
        raise OperationalError("UPDATE", {}, sqlite3.OperationalError("no such table: jobs"))

    with pytest.raises(OperationalError):
        await with_lock_retries(different)
    assert len(other) == 1


class _Result:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Session:
    def __init__(self, batches):
        self.batches = list(batches)
        self.commits = 0

    async def scalars(self, _statement):
        return _Result(self.batches.pop(0))

    async def commit(self):
        self.commits += 1


async def _noop(*_args, **_kwargs):
    return 1


@pytest.mark.parametrize(("every", "commits"), [(20, 2), (None, 0)])
def test_rescoring_saves_in_batches(monkeypatch, every, commits):
    monkeypatch.setattr(profiles_module, "refresh_movie_release_scores", _noop)
    monkeypatch.setattr(profiles_module, "refresh_show_release_scores", _noop)
    db = _Session([[uuid4() for _ in range(35)], [uuid4() for _ in range(5)]])
    result = asyncio.run(profiles_module.refresh_all_release_scores(db, commit_every=every))
    assert db.commits == commits
    assert result == SimpleNamespace(movies=35, shows=5, movie_releases=35, show_releases=5).__dict__
