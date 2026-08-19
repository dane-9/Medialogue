import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base
from app.models import *  # noqa: F401,F403

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without importing or requiring a synchronous PG driver."""

    # Alembic only needs the dialect while operating offline.  Using the
    # synchronous dialect spelling keeps generated SQL conventional while the
    # runtime application continues to depend only on asyncpg.
    url = get_settings().database_url.replace("+asyncpg", "")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Run online migrations through SQLAlchemy's async engine.

    Production uses ``postgresql+asyncpg``.  Earlier revisions stripped the
    ``+asyncpg`` suffix and accidentally required an uninstalled synchronous
    PostgreSQL DBAPI during container startup.  Keeping the async URL here
    means the same installed driver is used by both FastAPI and Alembic.
    """

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_settings().database_url
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    url = get_settings().database_url
    if "+asyncpg" in url or "+aiosqlite" in url:
        asyncio.run(_run_async_migrations())
        return

    # Keep synchronous URLs usable for lightweight migration regression tests
    # and operator-supplied synchronous databases, while production's
    # postgresql+asyncpg path remains fully async.
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
