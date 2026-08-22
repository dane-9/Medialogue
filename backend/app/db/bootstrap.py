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

