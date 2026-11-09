from __future__ import annotations


from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import hash_password
from app.models.auth import AdminUser


async def production_bootstrap_required(session: AsyncSession) -> bool:
    admins = (await session.scalars(select(AdminUser).order_by(AdminUser.created_at, AdminUser.id))).all()
    return not admins or (len(admins) == 1 and admins[0].is_default_password)


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

    rows = (await session.scalars(select(QualityDefinition))).all()
    existing = {item.name: item for item in rows}
    canonical_names: set[str] = set()
    for definition in list_quality_definitions():
        canonical_names.add(definition.name)
        item = existing.get(definition.name)
        if item is None:
            item = QualityDefinition(name=definition.name)
            session.add(item)
        item.resolution = definition.resolution
        item.source = definition.source
        item.modifier = definition.modifier
        item.scan_type = definition.scan_type
        item.rank = definition.rank
        item.enabled = True
        item.parser_definition = {}
    for name, item in existing.items():
        if name not in canonical_names:
            item.enabled = False


async def ensure_default_quality_profile(session: AsyncSession) -> None:
    """Create the editable baseline profile once on a fresh installation.

    The profile is deliberately not marked as application-owned. Once it
    exists, operators can rename it, reorder qualities, and change every
    score without the next application start undoing their choices.
    """

    from app.models.domain import CustomFormat, ProfileCustomFormatScore, QualityDefinition, QualityProfile
    from app.services.builtin_formats import (
        BUILTIN_FORMATS,
        DEFAULT_EXCLUDED_QUALITY_DEFINITIONS,
        DEFAULT_FORMAT_SCORES,
        DEFAULT_FORMAT_SCORES_VERSION,
        DEFAULT_QUALITY_PROFILE_MARKER,
        DEFAULT_QUALITY_PROFILE_NAME,
    )

    existing = await session.scalar(
        select(QualityProfile).where(func.lower(QualityProfile.name) == DEFAULT_QUALITY_PROFILE_NAME.casefold())
    )
    if existing is not None:
        return
    profiles = (await session.scalars(select(QualityProfile))).all()
    if any((profile.settings or {}).get("system_profile") == DEFAULT_QUALITY_PROFILE_MARKER for profile in profiles):
        return

    qualities = (
        await session.scalars(
            select(QualityDefinition)
            .where(QualityDefinition.enabled.is_(True))
            .order_by(QualityDefinition.rank.desc(), QualityDefinition.name)
        )
    ).all()
    formats = (await session.scalars(select(CustomFormat).where(CustomFormat.builtin.is_(True)))).all()
    formats_by_key = {row.builtin_key: row for row in formats if row.builtin_key}

    profile = QualityProfile(
        name=DEFAULT_QUALITY_PROFILE_NAME,
        settings={
            "quality_definition_ids": [
                str(item.id) for item in qualities
                if item.name not in DEFAULT_EXCLUDED_QUALITY_DEFINITIONS
            ],
            "system_profile": DEFAULT_QUALITY_PROFILE_MARKER,
            "default_scores_version": DEFAULT_FORMAT_SCORES_VERSION,
            "default_profile": True,
            "season_pack_upgrades": True,
        },
        revision=1,
    )
    session.add(profile)
    await session.flush()
    for builtin in BUILTIN_FORMATS:
        row = formats_by_key.get(builtin.key)
        if row is not None:
            session.add(
                ProfileCustomFormatScore(
                    profile_id=profile.id,
                    custom_format_id=row.id,
                    score=DEFAULT_FORMAT_SCORES[builtin.key],
                )
            )
    await session.flush()


async def refresh_default_profile_scores(session: AsyncSession) -> int:
    """Move the Default profile's untouched scores to the current defaults.

    Only a score still at its first default value changes; one the operator
    set stays. Runs once per defaults version. Returns how many changed.
    """

    from app.models.domain import CustomFormat, ProfileCustomFormatScore, QualityProfile
    from app.services.builtin_formats import (
        DEFAULT_FORMAT_SCORES,
        DEFAULT_FORMAT_SCORES_V1,
        DEFAULT_FORMAT_SCORES_VERSION,
        DEFAULT_QUALITY_PROFILE_MARKER,
        FORMATS_ADDED_IN_SCORES_V3,
    )

    changed = 0
    keys = {row.id: row.builtin_key for row in (await session.scalars(select(CustomFormat).where(CustomFormat.builtin.is_(True)))).all()}
    ids_by_key = {key: format_id for format_id, key in keys.items()}
    for profile in (await session.scalars(select(QualityProfile))).all():
        settings = dict(profile.settings or {})
        if settings.get("system_profile") != DEFAULT_QUALITY_PROFILE_MARKER:
            continue
        if int(settings.get("default_scores_version") or 1) >= DEFAULT_FORMAT_SCORES_VERSION:
            continue
        for row in (await session.scalars(select(ProfileCustomFormatScore).where(ProfileCustomFormatScore.profile_id == profile.id))).all():
            key = keys.get(row.custom_format_id)
            if key in DEFAULT_FORMAT_SCORES and row.score == DEFAULT_FORMAT_SCORES_V1.get(key) and row.score != DEFAULT_FORMAT_SCORES[key]:
                row.score = DEFAULT_FORMAT_SCORES[key]
                changed += 1
        # New in version 3: formats an existing profile has no score for yet.
        if int(settings.get("default_scores_version") or 1) < 3:
            scored = set(await session.scalars(
                select(ProfileCustomFormatScore.custom_format_id).where(ProfileCustomFormatScore.profile_id == profile.id)
            ))
            for key in FORMATS_ADDED_IN_SCORES_V3:
                if key in ids_by_key and ids_by_key[key] not in scored:
                    session.add(ProfileCustomFormatScore(profile_id=profile.id, custom_format_id=ids_by_key[key], score=DEFAULT_FORMAT_SCORES[key]))
                    changed += 1
        profile.settings = {**settings, "default_scores_version": DEFAULT_FORMAT_SCORES_VERSION}
        if changed:
            profile.revision = (profile.revision or 1) + 1
    await session.flush()
    return changed


async def ensure_builtin_custom_formats(session: AsyncSession) -> bool:
    """Install and re-sync the Custom Formats Medialogue ships.

    Keyed on ``builtin_key`` so a definition can be corrected in a later build
    and reach existing installations. ``enabled`` is deliberately never
    overwritten: whether a format is on is the operator's decision, and a
    disabled built-in must stay disabled across restarts.
    """

    from app.models.domain import CustomFormat
    from app.services.builtin_formats import BUILTIN_FORMATS, BUILTIN_KEYS, condition_definition

    rows = (await session.scalars(select(CustomFormat).where(CustomFormat.builtin.is_(True)))).all()
    existing = {row.builtin_key: row for row in rows}
    original = {
        key: (
            row.name,
            row.description,
            dict(row.condition_definition or {}),
            row.builtin,
        )
        for key, row in existing.items()
    }
    changed = False

    managed: dict[str, CustomFormat] = {}
    for builtin in BUILTIN_FORMATS:
        row = existing.get(builtin.key)
        if row is None:
            row = CustomFormat(
                builtin=True,
                builtin_key=builtin.key,
                enabled=builtin.default_enabled,
                revision=1,
            )
            session.add(row)
            changed = True
        row.name = builtin.name
        row.description = builtin.description
        row.builtin = True
        managed[builtin.key] = row

    await session.flush()
    for builtin in BUILTIN_FORMATS:
        row = managed[builtin.key]
        row.condition_definition = condition_definition(
            builtin,
            supersedes=tuple(str(managed[key].id) for key in builtin.supersedes if key in managed),
        )
        previous = original.get(builtin.key)
        current = (row.name, row.description, dict(row.condition_definition or {}), row.builtin)
        if previous is not None and previous != current:
            changed = True

    # Withdrawn built-ins must disappear from the catalog. Score rows are
    # application configuration, not historical evidence, and cascade away;
    # release snapshots remain untouched.
    for key, row in existing.items():
        if key not in BUILTIN_KEYS:
            await session.delete(row)
            changed = True

    await session.flush()
    return changed
