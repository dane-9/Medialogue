from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.torznab import TorznabClient
from app.services.events import create_event
from app.services.integration_state import ConfiguredIndexer

TorznabClientFactory = Callable[..., TorznabClient]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def test_indexer_connection(
    *,
    url: str,
    api_key: str,
    timeout_seconds: int = 15,
    client_factory: TorznabClientFactory = TorznabClient,
) -> dict[str, object]:
    started = perf_counter()
    client = client_factory(url, api_key, timeout=float(timeout_seconds))
    try:
        result = await client.health()
        return {
            "status": "healthy",
            "title": result.get("title"),
            "latency_ms": round((perf_counter() - started) * 1000),
        }
    finally:
        await client.close()


async def refresh_indexer_health(
    db: AsyncSession,
    indexer: ConfiguredIndexer,
    *,
    client_factory: TorznabClientFactory = TorznabClient,
) -> dict[str, object]:
    previous = indexer.health
    indexer.last_checked_at = utcnow()
    try:
        result = await test_indexer_connection(
            url=indexer.torznab_url,
            api_key=indexer.api_key or "",
            timeout_seconds=indexer.timeout_seconds,
            client_factory=client_factory,
        )
    except Exception as exc:
        indexer.health = "unavailable"
        indexer.latency_ms = None
        indexer.last_error = str(exc)
        if previous != indexer.health:
            await create_event(
                db,
                "indexer.health",
                entity_type="indexer",
                entity_id=indexer.id,
                message=f"Indexer {indexer.name} is unavailable.",
                details={"status": "unavailable", "error": str(exc)},
            )
        return {"status": "unavailable", "message": str(exc)}

    indexer.health = "healthy"
    indexer.last_success_at = utcnow()
    indexer.latency_ms = int(result.get("latency_ms") or 0)
    indexer.last_error = None
    if previous != indexer.health:
        await create_event(
            db,
            "indexer.health",
            entity_type="indexer",
            entity_id=indexer.id,
            message=f"Indexer {indexer.name} is healthy.",
            details={"status": "healthy"},
        )
    return result
