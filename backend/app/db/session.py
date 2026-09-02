from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


def _create_engine(database_url: str):
    url = make_url(database_url)
    if url.drivername != "sqlite+aiosqlite":
        raise ValueError("The application runtime requires a sqlite+aiosqlite database URL")
    engine = create_async_engine(database_url, pool_pre_ping=True)

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return engine


settings = get_settings()
engine = _create_engine(settings.database_url)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def configure_database(database_url: str) -> None:
    """Replace the process-local engine (primarily useful for test apps)."""
    global engine, async_session_factory
    engine = _create_engine(database_url)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
