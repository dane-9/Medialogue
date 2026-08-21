from pathlib import Path
from uuid import uuid4

import pytest

from app.core.integration_config import (
    DownloadClientConfig,
    IndexerConfig,
    IntegrationConfigStore,
)


def test_file_backed_integration_configuration_encrypts_secrets(tmp_path: Path) -> None:
    store = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    store.ensure_initialized()

    plex = store.save_plex(url="http://plex:32400", token="plex-token-value", enabled=True)
    tmdb = store.save_tmdb(api_key="tmdb-key-value", enabled=True)
    client_id = uuid4()
    store.save_download_client(
        DownloadClientConfig(
            id=client_id,
            name="Movies qBit",
            url="http://qbittorrent:8080",
            username="admin",
            password="qbit-password-value",
            scope="movies",
        )
    )
    indexer_id = uuid4()
    store.save_indexer(
        IndexerConfig(
            id=indexer_id,
            name="Prowlarr Movies",
            torznab_url="http://prowlarr:9696/api/v1/indexer/1/newznab",
            api_key="indexer-key-value",
            scope="movies",
        )
    )

    readable = (tmp_path / "medialogue.json").read_text(encoding="utf-8")
    encrypted = (tmp_path / "secrets.enc").read_bytes()
    for secret in ("plex-token-value", "tmdb-key-value", "qbit-password-value", "indexer-key-value"):
        assert secret not in readable
        assert secret.encode() not in encrypted

    assert store.get_plex() == plex
    assert store.get_tmdb() == tmdb
    assert store.get_download_client(client_id).password == "qbit-password-value"
    assert store.get_indexer(indexer_id).api_key == "indexer-key-value"
    assert (tmp_path / "medialogue.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "secrets.enc").stat().st_mode & 0o777 == 0o600


def test_configuration_revision_and_write_only_secret_updates(tmp_path: Path) -> None:
    store = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    first = store.save_plex(url="http://plex:32400", token="first-token", enabled=True)
    second = store.save_plex(
        url="http://plex-new:32400",
        token=None,
        enabled=False,
        expected_revision=first.revision,
    )
    assert second.revision == first.revision + 1
    assert second.token == "first-token"
    assert second.url == "http://plex-new:32400"
    assert second.enabled is False

    with pytest.raises(ValueError, match="revision_conflict"):
        store.save_plex(
            url="http://stale:32400",
            token=None,
            enabled=True,
            expected_revision=first.revision,
        )


def test_secret_key_change_refuses_to_decrypt_existing_config(tmp_path: Path) -> None:
    store = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    store.save_tmdb(api_key="tmdb-key-value", enabled=True)

    wrong_key_store = IntegrationConfigStore(tmp_path, "different-integration-secret-123456")
    with pytest.raises(RuntimeError, match="MEDIALOGUE_SECRET_KEY changed"):
        wrong_key_store.get_tmdb()


def test_fresh_config_has_no_database_import_behavior(tmp_path: Path) -> None:
    store = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    store.ensure_initialized()
    assert store.get_plex() is None
    assert store.get_tmdb() is None
    assert store.list_download_clients() == []
    assert store.list_indexers() == []
    assert not hasattr(store, "import_legacy")


def test_unknown_config_schema_is_rejected(tmp_path: Path) -> None:
    store = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    store.ensure_initialized()
    config_path = tmp_path / "medialogue.json"
    payload = config_path.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": 99')
    config_path.write_text(payload, encoding="utf-8")

    with pytest.raises(RuntimeError, match="Unsupported medialogue.json schema_version"):
        store.get_plex()


def test_incomplete_config_pair_is_rejected_instead_of_recreating_secrets(tmp_path: Path) -> None:
    store = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    store.ensure_initialized()
    (tmp_path / "secrets.enc").unlink()

    fresh_store = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    with pytest.raises(RuntimeError, match="Incomplete integration configuration"):
        fresh_store.ensure_initialized()
