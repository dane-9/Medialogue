from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_CONFIG_FILENAME = "medialogue.json"
_SECRETS_FILENAME = "secrets.enc"
_SECRETS_MAGIC = b"MEDIALOGUE-SECRETS-V1\n"
_SECRETS_AAD = b"medialogue-secrets-v1"
_SCHEMA_VERSION = 1


@dataclass(slots=True)
class PlexConfig:
    id: UUID
    url: str
    enabled: bool = True
    revision: int = 1
    token: str | None = None


@dataclass(slots=True)
class TMDBConfig:
    id: UUID
    enabled: bool = True
    revision: int = 1
    api_key: str | None = None


@dataclass(slots=True)
class DownloadClientConfig:
    id: UUID
    name: str
    url: str
    username: str | None
    scope: str
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    revision: int = 1
    poll_interval_seconds: int = 30
    recent_priority: str = "last"
    older_priority: str = "last"
    sequential_order: bool = False
    first_last_first: bool = False
    content_layout: str = "default"
    completed_download_handling: bool = True
    password: str | None = None


@dataclass(slots=True)
class IndexerConfig:
    id: UUID
    name: str
    torznab_url: str
    scope: str
    enabled: bool = True
    timeout_seconds: int = 15
    enable_rss: bool = True
    enable_interactive_search: bool = True
    categories: list[int] = field(default_factory=list)
    minimum_seeders: int = 1
    priority: int = 25
    revision: int = 1
    api_key: str | None = None


class IntegrationConfigStore:
    """File-backed source of truth for integration configuration.

    Non-secret settings live in ``/config/medialogue.json``. Secrets are kept
    in a separate AES-GCM encrypted file keyed from ``MEDIALOGUE_SECRET_KEY``.
    PostgreSQL stores only runtime/health state and durable references by UUID.
    """

    def __init__(self, config_dir: str | Path, secret_key: str):
        self.config_dir = Path(config_dir)
        self.config_path = self.config_dir / _CONFIG_FILENAME
        self.secrets_path = self.config_dir / _SECRETS_FILENAME
        self._key = hashlib.sha256(("medialogue:integration-secrets:" + secret_key).encode("utf-8")).digest()
        self._lock = threading.RLock()
        self._config_cache: dict[str, Any] | None = None
        self._secrets_cache: dict[str, Any] | None = None
        self._config_mtime_ns: int | None = None
        self._secrets_mtime_ns: int | None = None

    def _default_config(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "plex": None,
            "tmdb": None,
            "download_clients": [],
            "indexers": [],
            "library": {"count_specials": True},
        }

    def _default_secrets(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "plex_token": None,
            "tmdb_api_key": None,
            "download_client_passwords": {},
            "indexer_api_keys": {},
        }

    def _read_json(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return self._default_config()
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read {self.config_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{self.config_path} must contain a JSON object.")
        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported {self.config_path.name} schema_version {payload.get('schema_version')!r}; "
                f"expected {_SCHEMA_VERSION}."
            )
        merged = self._default_config()
        merged.update(payload)
        return merged

    def _read_secrets(self) -> dict[str, Any]:
        if not self.secrets_path.exists():
            return self._default_secrets()
        try:
            raw = self.secrets_path.read_bytes()
            if not raw.startswith(_SECRETS_MAGIC):
                raise ValueError("unsupported secrets file format")
            packed = base64.urlsafe_b64decode(raw[len(_SECRETS_MAGIC) :].strip())
            if len(packed) < 13:
                raise ValueError("truncated encrypted payload")
            nonce, ciphertext = packed[:12], packed[12:]
            plaintext = AESGCM(self._key).decrypt(nonce, ciphertext, _SECRETS_AAD)
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"Could not decrypt {self.secrets_path}. The file may be corrupt or MEDIALOGUE_SECRET_KEY changed."
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{self.secrets_path} decrypted to an invalid payload.")
        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported {self.secrets_path.name} schema_version {payload.get('schema_version')!r}; "
                f"expected {_SCHEMA_VERSION}."
            )
        merged = self._default_secrets()
        merged.update(payload)
        return merged

    def ensure_initialized(self) -> None:
        """Create the file-backed configuration on a fresh install.

        There is deliberately no database migration/import path. If these
        files do not exist, Medialogue starts with empty integration settings.
        """

        with self._lock:
            config_exists = self.config_path.exists()
            secrets_exist = self.secrets_path.exists()
            if config_exists and secrets_exist:
                # Force a read/decrypt so startup fails clearly if the secret
                # key changed or either file is corrupt. Also discard the old
                # indexer -> download-client coupling: download destination is
                # now chosen explicitly by the acquisition workflow.
                config, secrets = self._load()
                changed = False
                for row in config.get("indexers") or []:
                    if isinstance(row, dict) and "download_client_id" in row:
                        row.pop("download_client_id", None)
                        changed = True
                if changed:
                    self._save(config, secrets)
                return
            if config_exists != secrets_exist:
                present = self.config_path.name if config_exists else self.secrets_path.name
                missing = self.secrets_path.name if config_exists else self.config_path.name
                raise RuntimeError(
                    f"Incomplete integration configuration: {present} exists but {missing} is missing. "
                    "Restore both files together, or remove the remaining file to start with empty integration settings."
                )
            self._save(self._default_config(), self._default_secrets())

    def export_for_recovery(self) -> dict[str, Any]:
        """Return a decrypted snapshot for the already-sensitive Recovery Bundle."""

        config, secrets = self._load()
        return {"configuration": config, "secrets": secrets}

    def _load(self) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            config_mtime = self.config_path.stat().st_mtime_ns if self.config_path.exists() else None
            secrets_mtime = self.secrets_path.stat().st_mtime_ns if self.secrets_path.exists() else None
            if self._config_cache is None or config_mtime != self._config_mtime_ns:
                self._config_cache = self._read_json()
                self._config_mtime_ns = config_mtime
            if self._secrets_cache is None or secrets_mtime != self._secrets_mtime_ns:
                self._secrets_cache = self._read_secrets()
                self._secrets_mtime_ns = secrets_mtime
            # Deep-copy through JSON so callers cannot mutate cached state.
            return (
                json.loads(json.dumps(self._config_cache)),
                json.loads(json.dumps(self._secrets_cache)),
            )

    def _atomic_write(self, path: Path, data: bytes) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.config_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            os.chmod(path, 0o600)
            try:
                directory_fd = os.open(self.config_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def _save(self, config: dict[str, Any], secrets: dict[str, Any]) -> None:
        with self._lock:
            config["schema_version"] = _SCHEMA_VERSION
            secrets["schema_version"] = _SCHEMA_VERSION
            config_bytes = (json.dumps(config, indent=2, sort_keys=True) + "\n").encode("utf-8")
            plaintext = json.dumps(secrets, separators=(",", ":"), sort_keys=True).encode("utf-8")
            nonce = os.urandom(12)
            ciphertext = AESGCM(self._key).encrypt(nonce, plaintext, _SECRETS_AAD)
            secret_bytes = _SECRETS_MAGIC + base64.urlsafe_b64encode(nonce + ciphertext) + b"\n"
            # Write secrets first so a config reference is never committed before
            # its corresponding credential has durable storage.
            self._atomic_write(self.secrets_path, secret_bytes)
            self._atomic_write(self.config_path, config_bytes)
            self._config_cache = json.loads(json.dumps(config))
            self._secrets_cache = json.loads(json.dumps(secrets))
            self._config_mtime_ns = self.config_path.stat().st_mtime_ns
            self._secrets_mtime_ns = self.secrets_path.stat().st_mtime_ns

    @staticmethod
    def _uuid(value: Any) -> UUID:
        return value if isinstance(value, UUID) else UUID(str(value))

    def get_plex(self) -> PlexConfig | None:
        config, secrets = self._load()
        row = config.get("plex")
        if not isinstance(row, dict):
            return None
        return PlexConfig(
            id=self._uuid(row["id"]),
            url=str(row.get("url") or ""),
            enabled=bool(row.get("enabled", True)),
            revision=int(row.get("revision", 1)),
            token=secrets.get("plex_token") or None,
        )

    def save_plex(self, *, id: UUID | None = None, url: str, token: str | None, enabled: bool, expected_revision: int | None = None) -> PlexConfig:
        config, secrets = self._load()
        current = config.get("plex") if isinstance(config.get("plex"), dict) else None
        if current and expected_revision is not None and int(current.get("revision", 1)) != expected_revision:
            raise ValueError("revision_conflict")
        if current:
            item_id = self._uuid(current["id"])
            revision = int(current.get("revision", 1)) + 1
        else:
            item_id = id or uuid4()
            revision = 1
        if token:
            secrets["plex_token"] = token
        elif not current:
            raise ValueError("secret_required")
        config["plex"] = {"id": str(item_id), "url": url.rstrip("/"), "enabled": bool(enabled), "revision": revision}
        self._save(config, secrets)
        return self.get_plex()  # type: ignore[return-value]

    def get_tmdb(self) -> TMDBConfig | None:
        config, secrets = self._load()
        row = config.get("tmdb")
        if not isinstance(row, dict):
            return None
        return TMDBConfig(
            id=self._uuid(row["id"]),
            enabled=bool(row.get("enabled", True)),
            revision=int(row.get("revision", 1)),
            api_key=secrets.get("tmdb_api_key") or None,
        )

    def save_tmdb(self, *, id: UUID | None = None, api_key: str | None, enabled: bool, expected_revision: int | None = None) -> TMDBConfig:
        config, secrets = self._load()
        current = config.get("tmdb") if isinstance(config.get("tmdb"), dict) else None
        if current and expected_revision is not None and int(current.get("revision", 1)) != expected_revision:
            raise ValueError("revision_conflict")
        if current:
            item_id = self._uuid(current["id"])
            revision = int(current.get("revision", 1)) + 1
        else:
            item_id = id or uuid4()
            revision = 1
        if api_key:
            secrets["tmdb_api_key"] = api_key
        elif not current:
            raise ValueError("secret_required")
        config["tmdb"] = {"id": str(item_id), "enabled": bool(enabled), "revision": revision}
        self._save(config, secrets)
        return self.get_tmdb()  # type: ignore[return-value]

    def get_count_specials(self) -> bool:
        """Whether Season 0 counts toward show episode totals, library-wide.

        One stored answer rather than a per-show habit: Specials are almost
        always wanted everywhere or nowhere. This is purely about counting and
        says nothing about whether Specials are searched for.
        """

        config, _ = self._load()
        library = config.get("library")
        if not isinstance(library, dict):
            return True
        return bool(library.get("count_specials", True))

    def save_count_specials(self, value: bool) -> bool:
        config, secrets = self._load()
        library = config.get("library")
        if not isinstance(library, dict):
            library = {}
        library["count_specials"] = bool(value)
        config["library"] = library
        self._save(config, secrets)
        return bool(value)

    def list_download_clients(self) -> list[DownloadClientConfig]:
        config, secrets = self._load()
        passwords = secrets.get("download_client_passwords") or {}
        items: list[DownloadClientConfig] = []
        for row in config.get("download_clients") or []:
            if not isinstance(row, dict):
                continue
            item_id = self._uuid(row["id"])
            items.append(DownloadClientConfig(
                id=item_id,
                name=str(row.get("name") or ""),
                url=str(row.get("url") or ""),
                username=row.get("username") or None,
                scope=str(row.get("scope") or "movies"),
                category=row.get("category") or None,
                tags=list(row.get("tags") or []),
                enabled=bool(row.get("enabled", True)),
                revision=int(row.get("revision", 1)),
                poll_interval_seconds=int(row.get("poll_interval_seconds", 30)),
                recent_priority=str(row.get("recent_priority") or "last"),
                older_priority=str(row.get("older_priority") or "last"),
                sequential_order=bool(row.get("sequential_order", False)),
                first_last_first=bool(row.get("first_last_first", False)),
                content_layout=str(row.get("content_layout") or "default"),
                completed_download_handling=bool(row.get("completed_download_handling", True)),
                password=passwords.get(str(item_id)) or None,
            ))
        return items

    def get_download_client(self, client_id: UUID | str) -> DownloadClientConfig | None:
        target = self._uuid(client_id)
        return next((item for item in self.list_download_clients() if item.id == target), None)

    def save_download_client(self, item: DownloadClientConfig, *, expected_revision: int | None = None, preserve_blank_password: bool = True) -> DownloadClientConfig:
        config, secrets = self._load()
        rows = list(config.get("download_clients") or [])
        index = next((i for i, row in enumerate(rows) if isinstance(row, dict) and str(row.get("id")) == str(item.id)), None)
        current = rows[index] if index is not None else None
        if current and expected_revision is not None and int(current.get("revision", 1)) != expected_revision:
            raise ValueError("revision_conflict")
        revision = int(current.get("revision", 1)) + 1 if current else 1
        row = {
            "id": str(item.id), "name": item.name, "url": item.url.rstrip("/"), "username": item.username,
            "scope": item.scope, "category": item.category, "tags": list(item.tags), "enabled": bool(item.enabled),
            "revision": revision, "poll_interval_seconds": int(item.poll_interval_seconds),
            "recent_priority": item.recent_priority, "older_priority": item.older_priority,
            "sequential_order": bool(item.sequential_order), "first_last_first": bool(item.first_last_first),
            "content_layout": item.content_layout, "completed_download_handling": bool(item.completed_download_handling),
        }
        if index is None:
            rows.append(row)
        else:
            rows[index] = row
        config["download_clients"] = rows
        passwords = dict(secrets.get("download_client_passwords") or {})
        if item.password:
            passwords[str(item.id)] = item.password
        elif not current and not preserve_blank_password:
            passwords.pop(str(item.id), None)
        secrets["download_client_passwords"] = passwords
        self._save(config, secrets)
        return self.get_download_client(item.id)  # type: ignore[return-value]

    def delete_download_client(self, client_id: UUID | str) -> None:
        target = str(self._uuid(client_id))
        config, secrets = self._load()
        config["download_clients"] = [row for row in config.get("download_clients") or [] if str(row.get("id")) != target]
        passwords = dict(secrets.get("download_client_passwords") or {})
        passwords.pop(target, None)
        secrets["download_client_passwords"] = passwords
        self._save(config, secrets)

    def list_indexers(self) -> list[IndexerConfig]:
        config, secrets = self._load()
        keys = secrets.get("indexer_api_keys") or {}
        items: list[IndexerConfig] = []
        for row in config.get("indexers") or []:
            if not isinstance(row, dict):
                continue
            item_id = self._uuid(row["id"])
            items.append(IndexerConfig(
                id=item_id, name=str(row.get("name") or ""), torznab_url=str(row.get("torznab_url") or ""),
                scope=str(row.get("scope") or "both"), enabled=bool(row.get("enabled", True)),
                timeout_seconds=int(row.get("timeout_seconds", 15)), revision=int(row.get("revision", 1)),
                enable_rss=bool(row.get("enable_rss", True)),
                enable_interactive_search=bool(row.get("enable_interactive_search", True)),
                categories=[int(value) for value in (row.get("categories") or [])],
                minimum_seeders=int(row.get("minimum_seeders", 1)), priority=int(row.get("priority", 25)),
                api_key=keys.get(str(item_id)) or None,
            ))
        return items

    def get_indexer(self, indexer_id: UUID | str) -> IndexerConfig | None:
        target = self._uuid(indexer_id)
        return next((item for item in self.list_indexers() if item.id == target), None)

    def save_indexer(self, item: IndexerConfig, *, expected_revision: int | None = None) -> IndexerConfig:
        config, secrets = self._load()
        rows = list(config.get("indexers") or [])
        index = next((i for i, row in enumerate(rows) if isinstance(row, dict) and str(row.get("id")) == str(item.id)), None)
        current = rows[index] if index is not None else None
        if current and expected_revision is not None and int(current.get("revision", 1)) != expected_revision:
            raise ValueError("revision_conflict")
        revision = int(current.get("revision", 1)) + 1 if current else 1
        row = {"id": str(item.id), "name": item.name, "torznab_url": item.torznab_url.rstrip("/"), "scope": item.scope,
               "enabled": bool(item.enabled), "timeout_seconds": int(item.timeout_seconds), "revision": revision,
               "enable_rss": bool(item.enable_rss), "enable_interactive_search": bool(item.enable_interactive_search),
               "categories": list(item.categories), "minimum_seeders": int(item.minimum_seeders), "priority": int(item.priority)}
        if index is None:
            rows.append(row)
        else:
            rows[index] = row
        config["indexers"] = rows
        keys = dict(secrets.get("indexer_api_keys") or {})
        if item.api_key:
            keys[str(item.id)] = item.api_key
        secrets["indexer_api_keys"] = keys
        self._save(config, secrets)
        return self.get_indexer(item.id)  # type: ignore[return-value]

    def delete_indexer(self, indexer_id: UUID | str) -> None:
        target = str(self._uuid(indexer_id))
        config, secrets = self._load()
        config["indexers"] = [row for row in config.get("indexers") or [] if str(row.get("id")) != target]
        keys = dict(secrets.get("indexer_api_keys") or {})
        keys.pop(target, None)
        secrets["indexer_api_keys"] = keys
        self._save(config, secrets)

_store_lock = threading.Lock()
_store_key: tuple[str, str] | None = None
_store: IntegrationConfigStore | None = None


def get_integration_config_store() -> IntegrationConfigStore:
    global _store_key, _store
    settings = get_settings()
    key = (settings.config_dir, settings.secret_key)
    with _store_lock:
        if _store is None or _store_key != key:
            _store = IntegrationConfigStore(settings.config_dir, settings.secret_key)
            _store_key = key
        return _store


def reset_integration_config_store() -> None:
    global _store_key, _store
    with _store_lock:
        _store_key = None
        _store = None
