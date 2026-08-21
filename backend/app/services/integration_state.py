from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.integration_config import (
    DownloadClientConfig,
    IndexerConfig,
    PlexConfig,
    TMDBConfig,
    get_integration_config_store,
)
from app.models.domain import DownloadClient, Indexer, MediaScope, MediaType, PlexConfiguration, TMDBConfiguration


class _RuntimeProxy:
    __slots__ = ("state", "config")

    def __init__(self, state: Any, config: Any):
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "config", config)

    def __getattr__(self, name: str) -> Any:
        config = object.__getattribute__(self, "config")
        if hasattr(config, name):
            return getattr(config, name)
        state = object.__getattribute__(self, "state")
        return getattr(state, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"state", "config"}:
            object.__setattr__(self, name, value)
            return
        config = object.__getattribute__(self, "config")
        if hasattr(config, name):
            raise AttributeError(f"{name} is file-backed configuration and must be updated through IntegrationConfigStore")
        setattr(object.__getattribute__(self, "state"), name, value)


class ConfiguredDownloadClient(_RuntimeProxy):
    state: DownloadClient
    config: DownloadClientConfig

    @property
    def scope(self) -> MediaType:
        return MediaType(self.config.scope)


class ConfiguredIndexer(_RuntimeProxy):
    state: Indexer
    config: IndexerConfig

    @property
    def scope(self) -> MediaScope:
        return MediaScope(self.config.scope)


class ConfiguredPlex(_RuntimeProxy):
    state: PlexConfiguration
    config: PlexConfig


class ConfiguredTMDB(_RuntimeProxy):
    state: TMDBConfiguration
    config: TMDBConfig


async def _ensure_state(db: AsyncSession, model: type, resource_id: UUID):
    row = await db.get(model, resource_id)
    if row is None:
        row = model(id=resource_id)
        db.add(row)
        await db.flush()
    return row


async def get_configured_plex(db: AsyncSession) -> ConfiguredPlex | None:
    config = get_integration_config_store().get_plex()
    if config is None:
        return None
    state = await _ensure_state(db, PlexConfiguration, config.id)
    return ConfiguredPlex(state, config)


async def get_configured_tmdb(db: AsyncSession) -> ConfiguredTMDB | None:
    config = get_integration_config_store().get_tmdb()
    if config is None:
        return None
    state = await _ensure_state(db, TMDBConfiguration, config.id)
    return ConfiguredTMDB(state, config)


async def get_configured_download_client(db: AsyncSession, client_id: UUID) -> ConfiguredDownloadClient | None:
    config = get_integration_config_store().get_download_client(client_id)
    if config is None:
        return None
    state = await _ensure_state(db, DownloadClient, config.id)
    return ConfiguredDownloadClient(state, config)


async def list_configured_download_clients(db: AsyncSession) -> list[ConfiguredDownloadClient]:
    items: list[ConfiguredDownloadClient] = []
    for config in get_integration_config_store().list_download_clients():
        state = await _ensure_state(db, DownloadClient, config.id)
        items.append(ConfiguredDownloadClient(state, config))
    return items


async def get_configured_indexer(db: AsyncSession, indexer_id: UUID) -> ConfiguredIndexer | None:
    config = get_integration_config_store().get_indexer(indexer_id)
    if config is None:
        return None
    state = await _ensure_state(db, Indexer, config.id)
    return ConfiguredIndexer(state, config)


async def list_configured_indexers(db: AsyncSession) -> list[ConfiguredIndexer]:
    items: list[ConfiguredIndexer] = []
    for config in get_integration_config_store().list_indexers():
        state = await _ensure_state(db, Indexer, config.id)
        items.append(ConfiguredIndexer(state, config))
    return items


async def ensure_configured_integration_states(db: AsyncSession) -> None:
    await get_configured_plex(db)
    await get_configured_tmdb(db)
    await list_configured_download_clients(db)
    await list_configured_indexers(db)
