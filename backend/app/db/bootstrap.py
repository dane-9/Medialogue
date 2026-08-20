from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import hash_password
from app.models.auth import AdminUser


async def ensure_default_admin(session: AsyncSession, settings: Settings) -> AdminUser:
    result = await session.execute(select(AdminUser).where(AdminUser.username == settings.default_admin_username))
    admin = result.scalar_one_or_none()
    if admin is None:
        admin = AdminUser(
            username=settings.default_admin_username,
            password_hash=hash_password(settings.default_admin_password),
            is_default_password=True,
        )
        session.add(admin)
        await session.flush()
    return admin


async def mark_running_jobs_interrupted(session: AsyncSession) -> None:
    from datetime import datetime, timezone

    from app.models.domain import Job, JobStatus

    now = datetime.now(timezone.utc)
    # Queued work is also interrupted after a process restart because the
    # in-memory task that would have started it no longer exists. Preserve the
    # record and make the reason explicit rather than pretending it can resume.
    await session.execute(
        Job.__table__.update()
        .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        .values(
            status=JobStatus.INTERRUPTED,
            finished_at=now,
            updated_at=now,
            revision=Job.revision + 1,
            error={"code": "APPLICATION_RESTARTED", "message": "The job was interrupted by an application restart."},
        )
    )


async def ensure_quality_definitions(session: AsyncSession) -> None:
    from app.models.domain import QualityDefinition
    from app.parser import list_quality_definitions

    existing = {item.name: item for item in (await session.scalars(select(QualityDefinition))).all()}
    canonical_names: set[str] = set()
    for definition in list_quality_definitions():
        canonical_names.add(definition.name)
        item = existing.get(definition.name)
        if item is None:
            item = QualityDefinition(name=definition.name)
            session.add(item)
        # Quality definitions are application-owned/hardcoded. Keep existing
        # installations synchronized with the canonical catalog on upgrade.
        item.resolution = definition.resolution
        item.source = definition.source
        item.modifier = definition.modifier
        item.scan_type = definition.scan_type
        item.rank = definition.rank
        item.enabled = True
        item.parser_definition = {}
    # Do not delete old rows because historical releases may reference them.
    # Mark definitions removed from the catalog disabled instead.
    for name, item in existing.items():
        if name not in canonical_names:
            item.enabled = False


async def ensure_problem_integrity(session: AsyncSession) -> None:
    """Repair legacy duplicate OPEN Problems and enforce one-row identity.

    The v9 baseline is model-driven, so an already-created v9 database does
    not automatically receive indexes added to the SQLAlchemy model later.
    This startup repair is deliberately idempotent and limited to the Problems
    table so existing TrueNAS/PostgreSQL volumes gain the same invariant as
    fresh installations.
    """

    from datetime import datetime, timezone

    from app.models.domain import Problem, ProblemStatus

    rows = (
        await session.scalars(
            select(Problem)
            .where(Problem.status == ProblemStatus.OPEN)
            .order_by(Problem.created_at.asc(), Problem.id.asc())
        )
    ).all()
    canonical: dict[tuple[str, str, object | None], Problem] = {}
    now = datetime.now(timezone.utc)
    for problem in rows:
        key = (problem.reason, problem.entity_type, problem.entity_id)
        retained = canonical.get(key)
        if retained is None:
            canonical[key] = problem
            continue
        problem.status = ProblemStatus.RESOLVED
        problem.resolved_at = now
        problem.resolution = {
            "action": "deduplicated_on_startup",
            "canonical_problem_id": str(retained.id),
        }
    await session.flush()

    # SQLAlchemy metadata creates these on fresh databases. CREATE IF NOT
    # EXISTS brings already-initialized databases to the same invariant. The
    # enum is persisted by SQLAlchemy using member names (OPEN/RESOLVED/etc.).
    await session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_problems_open_entity "
            "ON problems (reason, entity_type, entity_id) "
            "WHERE status = 'OPEN' AND entity_id IS NOT NULL"
        )
    )
    await session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_problems_open_global "
            "ON problems (reason, entity_type) "
            "WHERE status = 'OPEN' AND entity_id IS NULL"
        )
    )
