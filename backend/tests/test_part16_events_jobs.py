import asyncio
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.base import Base
from app.db import session as db_session
from app.db.bootstrap import mark_running_jobs_interrupted
from app.main import create_app
from app.models.domain import (
    AssociationType,
    Event,
    Job,
    JobStatus,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    Problem,
    ReleaseState,
    StorageRoot,
    Torrent,
)
from app.services.events import create_event, subscribe, unsubscribe
from app.services.jobs import create_job, update_job
from app.services.reconciliation import mark_root_available, mark_root_unavailable, open_problem


@pytest.fixture
def client():
    db_path = tempfile.mktemp(prefix="medialogue-part16-", suffix=".db", dir=os.getcwd())
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(database_url=database_url, bootstrap_admin=True, secret_key="test-secret-key-123456")
    engine = create_async_engine(database_url)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(db_session.engine.dispose())
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass


def login_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert login.status_code == 200
    return {"X-CSRF-Token": login.json()["csrf_token"]}


def test_job_status_is_persisted_and_published_live(client: TestClient) -> None:
    login_headers(client)
    async def scenario():
        queue = subscribe()
        try:
            async with db_session.async_session_factory() as db:
                job = await create_job(db, "part16_test", summary={"path": "/test"})
                await db.commit()
                queued = await asyncio.wait_for(queue.get(), timeout=1)
                assert queued["event"] == "job.status"
                assert queued["data"]["status"] == "queued"
                await update_job(db, job, status=JobStatus.RUNNING, progress={"percent": 25})
                await db.commit()
                running = await asyncio.wait_for(queue.get(), timeout=1)
                assert running["event"] == "job.status"
                assert running["data"]["status"] == "running"
                assert running["data"]["progress"]["percent"] == 25
                return job.id
        finally:
            unsubscribe(queue)

    job_id = asyncio.run(scenario())
    response = client.get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["progress"]["percent"] == 25


def test_problem_live_events_are_commit_bound_and_rollback_safe(client: TestClient) -> None:
    login_headers(client)

    async def scenario():
        queue = subscribe()
        try:
            async with db_session.async_session_factory() as db:
                rolled_back_id = uuid.uuid4()
                await open_problem(
                    db,
                    reason="ROLLBACK_ONLY",
                    entity_type="test",
                    entity_id=rolled_back_id,
                    message="This Problem must never reach SSE.",
                )
                assert queue.empty()
                await db.rollback()
                await asyncio.sleep(0)
                assert queue.empty()

            async with db_session.async_session_factory() as db:
                committed_id = uuid.uuid4()
                await open_problem(
                    db,
                    reason="COMMIT_ONLY",
                    entity_type="test",
                    entity_id=committed_id,
                    message="This Problem becomes visible after commit.",
                )
                assert queue.empty()
                await db.commit()
                queued = await asyncio.wait_for(queue.get(), timeout=1)
                assert queued["event"] == "problem.created"
                assert queued["entity_id"] == str(committed_id)
                assert queue.empty()
        finally:
            unsubscribe(queue)

    asyncio.run(scenario())


def test_open_problem_updates_one_canonical_row(client: TestClient) -> None:
    login_headers(client)

    async def scenario():
        entity_id = uuid.uuid4()
        async with db_session.async_session_factory() as db:
            first = await open_problem(
                db,
                reason="CANONICAL_PROBLEM",
                entity_type="test",
                entity_id=entity_id,
                message="First evidence",
                details={"affected_count": 1},
            )
            second = await open_problem(
                db,
                reason="CANONICAL_PROBLEM",
                entity_type="test",
                entity_id=entity_id,
                message="Updated evidence",
                details={"affected_count": 2},
            )
            await db.commit()
            rows = (
                await db.scalars(
                    select(Problem).where(
                        Problem.reason == "CANONICAL_PROBLEM",
                        Problem.entity_type == "test",
                        Problem.entity_id == entity_id,
                    )
                )
            ).all()
            assert first.id == second.id
            assert len(rows) == 1
            assert rows[0].message == "Updated evidence"
            assert rows[0].details == {"affected_count": 2}

    asyncio.run(scenario())


def test_cancelled_queued_job_is_not_resurrected_and_restart_interrupts_running_work(client: TestClient) -> None:
    headers = login_headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            queued = await create_job(db, "cancel_me")
            running = await create_job(db, "restart_me")
            await update_job(db, running, status=JobStatus.RUNNING)
            await db.commit()
            return queued.id, running.id

    queued_id, running_id = asyncio.run(seed())
    cancelled = client.post(f"/api/v1/jobs/{queued_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    async def interrupt():
        async with db_session.async_session_factory() as db:
            await mark_running_jobs_interrupted(db)
            await db.commit()
            cancelled_row = await db.get(Job, queued_id)
            running_row = await db.get(Job, running_id)
            return cancelled_row, running_row

    cancelled_row, running_row = asyncio.run(interrupt())
    assert cancelled_row.status == JobStatus.CANCELLED
    assert running_row.status == JobStatus.INTERRUPTED
    assert running_row.error["code"] == "APPLICATION_RESTARTED"
    assert running_row.finished_at is not None


def test_empty_root_scan_has_durable_history_and_survives_refresh(client: TestClient) -> None:
    headers = login_headers(client)
    client.put("/api/v1/operations", headers=headers, json={"enabled": True})
    root_path = Path(tempfile.mkdtemp(prefix="medialogue-empty-root-", dir=os.getcwd()))
    try:
        created = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": f"Empty-{uuid.uuid4().hex[:8]}", "path": str(root_path), "media_type": "movies"},
        )
        assert created.status_code == 201, created.text
        started = client.post(f"/api/v1/storage-roots/{created.json()['id']}/scan", headers=headers)
        assert started.status_code == 202
        job_id = started.json()["job_id"]

        deadline = time.monotonic() + 5.0
        second = None
        while time.monotonic() < deadline:
            second = client.get(f"/api/v1/jobs/{job_id}")
            assert second.status_code == 200
            if second.json()["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                break
            time.sleep(0.02)
        assert second is not None
        assert second.json()["status"] == "completed", second.json()
        assert second.json()["progress"]["percent"] == 100

        history = client.get("/api/v1/events?event_type=scan.completed")
        assert history.status_code == 200
        assert any(item["details"].get("matched") == 0 for item in history.json()["items"])
    finally:
        root_path.rmdir()


def test_problem_lifecycle_and_storage_health_have_meaningful_durable_events(client: TestClient) -> None:
    async def scenario():
        queue = subscribe()
        try:
            async with db_session.async_session_factory() as db:
                root = StorageRoot(
                    name=f"Offline-{uuid.uuid4().hex[:8]}",
                    resolved_root_path="/definitely/not/mounted",
                    media_type=MediaType.MOVIES,
                    last_health="available",
                )
                db.add(root)
                await db.flush()
                await mark_root_unavailable(db, root, error="offline")
                await mark_root_available(db, root)
                await db.commit()
                event_types = list(await db.scalars(select(Event.event_type).order_by(Event.created_at)))
            live_types = []
            while not queue.empty():
                live_types.append((await queue.get())["event"])
            return event_types, live_types
        finally:
            unsubscribe(queue)

    event_types, live_types = asyncio.run(scenario())
    assert "problem.created" in event_types
    assert "problem.resolved" in event_types
    assert "storage_root.unavailable" in event_types
    assert "storage_root.restored" in event_types
    assert live_types.count("storage_root.health") == 2


def test_movie_event_history_includes_related_release_and_torrent_evidence(client: TestClient) -> None:
    login_headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            movie = Movie(title="Event Movie", sort_title="event movie", year=2026, tmdb_id=987654)
            db.add(movie)
            await db.flush()
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name="Event Movie 2026 2160p WEB-DL-GRP",
                parsed_title="Event Movie",
                parsed_year=2026,
                release_state=ReleaseState.CURRENT,
            )
            torrent = Torrent(info_hash=uuid.uuid4().hex + "abcd1234", name="Event Movie 2026")
            db.add_all([release, torrent])
            await db.flush()
            db.add(MovieReleaseTorrent(movie_release_id=release.id, torrent_id=torrent.id, association_type=AssociationType.ATTACHED))
            await create_event(db, "release.replaced", entity_type="movie_release", entity_id=release.id, message="Release history event.")
            await create_event(db, "torrent.removed", entity_type="torrent", entity_id=torrent.id, message="Torrent history event.")
            await db.commit()

    asyncio.run(seed())
    response = client.get("/api/v1/movies/987654/events")
    assert response.status_code == 200, response.text
    types = {item["event_type"] for item in response.json()["items"]}
    assert {"release.replaced", "torrent.removed"}.issubset(types)

    filtered = client.get("/api/v1/events?event_type=torrent.removed&severity=info")
    assert filtered.status_code == 200
    assert filtered.json()["total"] >= 1
    assert all(item["event_type"] == "torrent.removed" for item in filtered.json()["items"])


def test_storage_scan_job_is_runtime_visible_deduplicated_and_cancellable(client: TestClient, monkeypatch) -> None:
    """Regression for the production bug where scans stayed QUEUED forever.

    The runtime worker must be independent from the request, persist RUNNING,
    deduplicate a second click, and accept cancellation immediately.
    """

    from app.api import storage as storage_api
    from app.services.jobs import update_job

    headers = login_headers(client)
    client.put("/api/v1/operations", headers=headers, json={"enabled": True})
    root_path = Path(tempfile.mkdtemp(prefix="medialogue-cancellable-root-", dir=os.getcwd()))
    started = threading.Event()
    cancelled = threading.Event()

    async def slow_scan(job_id, root_id):
        del root_id
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            assert job is not None
            await update_job(db, job, status=JobStatus.RUNNING, progress={"percent": 1, "stage": "test_wait"})
            await db.commit()
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(storage_api, "run_storage_root_scan", slow_scan)
    try:
        root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": f"Slow-{uuid.uuid4().hex[:8]}", "path": str(root_path), "media_type": "movies"},
        )
        assert root.status_code == 201, root.text
        root_id = root.json()["id"]

        first = client.post(f"/api/v1/storage-roots/{root_id}/scan", headers=headers)
        assert first.status_code == 202, first.text
        job_id = first.json()["job_id"]
        assert started.wait(2), "runtime scan worker never started"

        visible = client.get(f"/api/v1/jobs/{job_id}")
        assert visible.status_code == 200, visible.text
        assert visible.json()["status"] == "running"

        duplicate = client.post(f"/api/v1/storage-roots/{root_id}/scan", headers=headers)
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["job_id"] == job_id

        stopped = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "cancelled"
        assert cancelled.wait(2), "runtime scan task was not cancelled"
        durable = client.get(f"/api/v1/jobs/{job_id}")
        assert durable.json()["status"] == "cancelled"
    finally:
        if root_path.exists():
            root_path.rmdir()


def test_problem_count_endpoint_returns_only_a_count(client: TestClient) -> None:
    headers = login_headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            db.add_all([
                Problem(reason="ONE", entity_type="test", message="one"),
                Problem(reason="TWO", entity_type="test", message="two", status=ProblemStatus.RESOLVED),
            ])
            await db.commit()

    from app.models.domain import ProblemStatus
    asyncio.run(seed())
    response = client.get("/api/v1/problems/count?status=open", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"count": 1}
