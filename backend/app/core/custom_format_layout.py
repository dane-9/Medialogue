from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_FILENAME = "custom-format-layout.json"
_SCHEMA_VERSION = 1
_lock = threading.RLock()


def read_custom_format_layout() -> dict[str, Any] | None:
    path = Path(get_settings().config_dir) / _FILENAME
    with _lock:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
        raise RuntimeError(f"{path} has an unsupported Custom Format layout schema.")
    return payload


def save_custom_format_layout(sections: list[dict[str, Any]]) -> None:
    path = Path(get_settings().config_dir) / _FILENAME
    payload = {"schema_version": _SCHEMA_VERSION, "sections": sections}
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
