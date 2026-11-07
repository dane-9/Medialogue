import asyncio
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.base import Base
from app.db import session as db_session
from app.db.bootstrap import mark_running_jobs_interrupted
from app.main import create_app
from app.models.domain import (
    Event,
    Job,
    JobStatus,
    MediaDirectory,
    MediaFile,
    MediaType,
    Movie,
    Problem,
    Severity,
    StorageRoot,
    Schedule,
)
from app.services.events import create_event, subscribe, unsubscribe
from app.services.jobs import JobFailure, checkpoint_job, create_job, run_job, update_job
from app.services.housekeeping import prune_scheduled_job_history
from app.services.reconciliation import mark_root_available, mark_root_unavailable, open_problem


class _NoMatchTMDBClient:
    """Reachable TMDB that recognises nothing.

    Keeps these suites focused on their own subject: the scan completes, and
    identity simply stays unresolved.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def health(self):
        return {"status": "healthy"}

    async def search_movie(self, title: str, year: int | None = None):
        return []

    async def search_show(self, title: str, year: int | None = None):
        return []

    async def get_movie_alternative_titles(self, tmdb_id: int):
        return ()

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def _configure_fake_tmdb(monkeypatch):
    import app.api.tmdb as tmdb_api
    import app.services.tmdb as tmdb_service

    monkeypatch.setattr(tmdb_service, "TMDBClient", _NoMatchTMDBClient)
    monkeypatch.setattr(tmdb_api, "TMDBClient", _NoMatchTMDBClient)


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(database_url=database_url, bootstrap_admin=True, config_dir=f"{db_path}.config", secret_key="test-secret-key-123456")
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


def test_scheduled_health_task_can_be_listed_and_run_now(client: TestClient) -> None:
    headers = login_headers(client)
    response = client.get("/api/v1/tasks", headers=headers)
    assert response.status_code == 200, response.text
    listed = response.json()
    assert {item["job_type"] for item in listed} == {
        "integration_health_sweep",
        "verify_known_media_files",
        "full_media_folder_scan",
        "plex_library_sync",
        "search_media_upgrades",
        "scheduled_housekeeping",
        "remote_backup",
        "search_media_replacements",
        "readd_missing_torrents",
        "search_new_replacements",
    }
    task = next(item for item in listed if item["job_type"] == "integration_health_sweep")
    assert task["name"] == "Check Integration Health"
    assert task["interval_seconds"] == 60
    assert task["configurable"] is True
    assert 300 in task["interval_options"]
    assert task["next_run_at"] is not None
    assert task["next_run_at"].endswith(("Z", "+00:00"))

    full_scan = next(item for item in listed if item["job_type"] == "full_media_folder_scan")
    assert full_scan["interval_seconds"] == 21600
    assert full_scan["enabled"] is True
    assert full_scan["next_run_at"] is not None
    assert {3600, 7200, 14400, 21600} <= set(full_scan["interval_options"])

    plex_sync = next(item for item in listed if item["job_type"] == "plex_library_sync")
    assert plex_sync["name"] == "Sync Plex Library"
    assert plex_sync["configurable"] is True
    assert plex_sync["enabled"] is False
    assert plex_sync["next_run_at"] is None
    assert 86400 in plex_sync["interval_options"]

    upgrade_search = next(item for item in listed if item["job_type"] == "search_media_upgrades")
    assert upgrade_search["name"] == "Search for Media Upgrades"
    assert upgrade_search["interval_seconds"] == 86400
    assert upgrade_search["enabled"] is True
    assert upgrade_search["configurable"] is True
    assert {21600, 43200, 86400, 259200, 604800} <= set(upgrade_search["interval_options"])

    started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers)
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job
    assert job["summary"]["trigger"] == "manual"
    assert job["started_at"].endswith(("Z", "+00:00"))
    assert job["finished_at"].endswith(("Z", "+00:00"))

    refreshed = client.get("/api/v1/tasks", headers=headers).json()
    task = next(item for item in refreshed if item["id"] == task["id"])
    assert task["last_status"] == "completed"
    assert task["last_duration_ms"] is not None


def test_plex_library_sync_is_available_as_a_dedicated_task(client: TestClient) -> None:
    headers = login_headers(client)
    task = next(
        item
        for item in client.get("/api/v1/tasks", headers=headers).json()
        if item["job_type"] == "plex_library_sync"
    )

    started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers)
    assert started.status_code == 200, started.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{started.json()['job_id']}", headers=headers).json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert job["job_type"] == "plex_library_sync"
    assert job["status"] == "failed"
    assert job["error"]["code"] == "PLEX_NOT_CONFIGURED"
    assert job["summary"]["trigger"] == "manual"


def test_task_interval_can_be_changed_or_made_manual_only(client: TestClient) -> None:
    headers = login_headers(client)
    listed = client.get("/api/v1/tasks", headers=headers).json()
    health = next(item for item in listed if item["job_type"] == "integration_health_sweep")
    full_scan = next(item for item in listed if item["job_type"] == "full_media_folder_scan")
    housekeeping = next(item for item in listed if item["job_type"] == "scheduled_housekeeping")

    changed = client.patch(f"/api/v1/tasks/{health['id']}", headers=headers, json={"interval_seconds": 300})
    assert changed.status_code == 200, changed.text
    assert changed.json()["interval_seconds"] == 300
    assert changed.json()["enabled"] is True
    # Default seeding runs on every list request and must preserve the user's choice.
    persisted = next(item for item in client.get("/api/v1/tasks", headers=headers).json() if item["id"] == health["id"])
    assert persisted["interval_seconds"] == 300

    manual = client.patch(f"/api/v1/tasks/{full_scan['id']}", headers=headers, json={"interval_seconds": None})
    assert manual.status_code == 200, manual.text
    assert manual.json()["enabled"] is False
    assert manual.json()["next_run_at"] is None

    started = client.post(f"/api/v1/tasks/{full_scan['id']}/run", headers=headers)
    assert started.status_code == 200, started.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{started.json()['job_id']}", headers=headers).json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job

    fixed = client.patch(f"/api/v1/tasks/{housekeeping['id']}", headers=headers, json={"interval_seconds": 604800})
    assert fixed.status_code == 409
    assert fixed.json()["error"]["code"] == "TASK_INTERVAL_FIXED"

    invalid = client.patch(f"/api/v1/tasks/{health['id']}", headers=headers, json={"interval_seconds": 123})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "TASK_INTERVAL_INVALID"


def test_known_media_verification_checks_stored_files_without_discovery(client: TestClient) -> None:
    headers = login_headers(client)
    with tempfile.TemporaryDirectory() as root_path:
        known_folder = Path(root_path) / "Known Movie"
        known_folder.mkdir()
        (Path(root_path) / "Unknown Movie").mkdir()

        async def seed() -> uuid.UUID:
            async with db_session.async_session_factory() as db:
                root = StorageRoot(
                    name="Movies",
                    resolved_root_path=root_path,
                    media_type=MediaType.MOVIES,
                    enabled=True,
                    missing_grace_checks=2,
                    last_scan_at=datetime.now(timezone.utc),
                )
                directory = MediaDirectory(storage_root=root, resolved_path=str(known_folder))
                media_file = MediaFile(media_directory=directory, relative_path="missing.mkv", filename="missing.mkv")
                db.add_all((root, directory, media_file))
                await db.commit()
                return media_file.id

        media_file_id = asyncio.run(seed())
        task = next(
            item
            for item in client.get("/api/v1/tasks", headers=headers).json()
            if item["job_type"] == "verify_known_media_files"
        )
        for _ in range(2):
            started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers)
            assert started.status_code == 200, started.text
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = client.get(f"/api/v1/jobs/{started.json()['job_id']}", headers=headers).json()
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)
            assert job["status"] == "completed", job

        async def inspect() -> tuple[bool, int, int]:
            async with db_session.async_session_factory() as db:
                media_file = await db.get(MediaFile, media_file_id)
                assert media_file is not None
                directory_count = len((await db.scalars(select(MediaDirectory))).all())
                return media_file.exists, media_file.missing_check_count, directory_count

        exists, missing_checks, directory_count = asyncio.run(inspect())
        assert exists is False
        assert missing_checks == 2
        assert directory_count == 1


def test_active_schedule_jobs_are_unique_and_history_pruning_is_scoped(client: TestClient) -> None:
    async def scenario():
        now = datetime.now(timezone.utc)
        async with db_session.async_session_factory() as db:
            schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "integration_health_sweep"))
            assert schedule is not None
            active = Job(job_type="integration_health_sweep", schedule_id=schedule.id, status=JobStatus.QUEUED)
            db.add(active)
            await db.commit()
            active_id = active.id

            db.add(Job(job_type="integration_health_sweep", schedule_id=schedule.id, status=JobStatus.RUNNING))
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

            active = await db.get(Job, active_id)
            assert active is not None
            active.status = JobStatus.COMPLETED
            active.started_at = now - timedelta(days=8, seconds=3)
            active.finished_at = now - timedelta(days=8)
            old_failure = Job(
                job_type="integration_health_sweep",
                status=JobStatus.FAILED,
                started_at=now - timedelta(days=31, seconds=2),
                finished_at=now - timedelta(days=31),
            )
            recent_health = Job(
                job_type="integration_health_sweep",
                status=JobStatus.COMPLETED,
                started_at=now - timedelta(hours=1, seconds=2),
                finished_at=now - timedelta(hours=1),
            )
            old_user_job = Job(
                job_type="interactive_search",
                status=JobStatus.COMPLETED,
                started_at=now - timedelta(days=90, seconds=2),
                finished_at=now - timedelta(days=90),
            )
            db.add_all((old_failure, recent_health, old_user_job))
            await db.commit()
            ids = {"old_health": active.id, "old_failure": old_failure.id, "recent": recent_health.id, "user": old_user_job.id}

            deleted = await prune_scheduled_job_history(db, now=now)
            await db.commit()
            remaining = set((await db.scalars(select(Job.id))).all())
            return deleted, ids, remaining

    deleted, ids, remaining = asyncio.run(scenario())
    assert deleted == 2
    assert ids["old_health"] not in remaining
    assert ids["old_failure"] not in remaining
    assert ids["recent"] in remaining
    assert ids["user"] in remaining


def test_scheduled_health_task_restores_plex_without_manual_plex_refresh(client: TestClient, monkeypatch) -> None:
    import app.services.health_sweep as health_sweep

    headers = login_headers(client)
    configured = client.put(
        "/api/v1/integrations/plex",
        headers=headers,
        json={"url": "http://plex.test:32400", "token": "plex-token", "enabled": True},
    )
    assert configured.status_code == 200, configured.text

    async def mark_healthy(db, configuration):
        configuration.health = "healthy"
        configuration.last_success_at = health_sweep.utcnow() if hasattr(health_sweep, "utcnow") else None
        configuration.last_error = None
        return {"status": "healthy", "latency_ms": 1}

    monkeypatch.setattr(health_sweep, "refresh_plex_health", mark_healthy)
    task = next(item for item in client.get("/api/v1/tasks", headers=headers).json() if item["job_type"] == "integration_health_sweep")
    started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers)
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job
    health = client.get("/api/v1/integrations/health", headers=headers).json()
    assert health["plex"]["status"] == "healthy"


def test_due_health_task_runs_without_an_open_browser(client: TestClient) -> None:
    headers = login_headers(client)

    async def make_due():
        async with db_session.async_session_factory() as db:
            schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "integration_health_sweep"))
            assert schedule is not None
            schedule.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()

    asyncio.run(make_due())
    deadline = time.monotonic() + 8
    scheduled_job = None
    while time.monotonic() < deadline:
        jobs = client.get("/api/v1/jobs?job_type=integration_health_sweep", headers=headers).json()["items"]
        scheduled_job = next((job for job in jobs if job["summary"].get("trigger") == "scheduled"), None)
        if scheduled_job and scheduled_job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)

    assert scheduled_job is not None
    assert scheduled_job["status"] == "completed", scheduled_job


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


def test_shared_job_runner_persists_worker_failure(client: TestClient) -> None:
    login_headers(client)

    async def scenario():
        async with db_session.async_session_factory() as db:
            job = await create_job(db, "shared_runner_test")
            await db.commit()
            job_id = job.id

        async def worker(db, job):
            await checkpoint_job(db, job, status=JobStatus.RUNNING, progress={"percent": 10, "stage": "working"})
            raise JobFailure(
                "SHARED_RUNNER_FAILED",
                "The worker failed.",
                details={"attempt": 1},
                progress={"percent": 50, "stage": "failed"},
            )

        await run_job(
            job_id,
            worker,
            failure_code="UNEXPECTED_SHARED_RUNNER_FAILURE",
            failure_message="Unexpected worker failure.",
        )

        async with db_session.async_session_factory() as db:
            return await db.get(Job, job_id)

    job = asyncio.run(scenario())
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.progress == {"percent": 50, "stage": "failed"}
    assert job.error == {"code": "SHARED_RUNNER_FAILED", "message": "The worker failed.", "details": {"attempt": 1}}


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
    client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
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


def test_storage_scan_job_is_runtime_visible_deduplicated_and_cancellable(client: TestClient, monkeypatch) -> None:
    """Regression for the production bug where scans stayed QUEUED forever.

    The runtime worker must be independent from the request, persist RUNNING,
    deduplicate a second click, and accept cancellation immediately.
    """

    from app.api import storage as storage_api
    from app.services.jobs import update_job

    headers = login_headers(client)
    client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
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


def test_events_sort_by_severity_ranks_worst_first(client: TestClient) -> None:
    """Severity is an enum column, so the ranking has to be explicit: sorting on
    the stored value would order it alphabetically instead."""

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            for severity in (Severity.INFO, Severity.ERROR, Severity.WARNING):
                await create_event(db, "scan.completed", entity_type="movie", message=f"Sort probe {severity.value}.", severity=severity)
            await db.commit()

    login_headers(client)
    asyncio.run(seed())

    worst_first = client.get("/api/v1/events?sort=severity")
    assert worst_first.status_code == 200, worst_first.text
    assert [item["severity"] for item in worst_first.json()["items"]][:3] == ["error", "warning", "info"]

    best_first = client.get("/api/v1/events?sort=-severity")
    assert [item["severity"] for item in best_first.json()["items"]][:3] == ["info", "warning", "error"]


def test_events_reject_a_sort_field_the_list_does_not_show(client: TestClient) -> None:
    login_headers(client)
    assert client.get("/api/v1/events?sort=message").status_code == 422


def test_events_accept_the_largest_page_size_the_list_offers(client: TestClient) -> None:
    login_headers(client)
    assert client.get("/api/v1/events?page_size=1000").status_code == 200
