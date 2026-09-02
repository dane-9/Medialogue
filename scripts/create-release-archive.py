#!/usr/bin/env python3
"""Build and verify a source release archive from an explicit allowlist.

The repository contains tests, local previews, generated working directories,
and operator configuration examples. A release archive is intentionally
assembled from the runtime allowlist below so those development and credential
surfaces cannot accidentally ship.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_FILES = frozenset(
    {
        ".dockerignore",
        ".env.example",
        ".env.truenas-dockge.example",
        "CHANGELOG.md",
        "Dockerfile",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "TRUENAS_DOCKGE_GHCR.md",
        "compose.truenas-dockge.yml",
        "docker-compose.yml",
        "backend/alembic.ini",
        "backend/pyproject.toml",
        "backend/requirements.lock",
        "docker/README.md",
        "docker/entrypoint.sh",
        "frontend/index.html",
        "frontend/package-lock.json",
        "frontend/package.json",
        "frontend/tsconfig.app.json",
        "frontend/tsconfig.json",
        "frontend/tsconfig.node.json",
        "frontend/vite.config.ts",
        "openapi/contract-manifest.json",
        "openapi/openapi.json",
        "scripts/create-release-archive.py",
        "scripts/generate_api_contract.py",
        "scripts/release-check.sh",
        "scripts/verify.sh",
    }
)
ALLOWED_DIRECTORIES = ("backend/app/", "backend/alembic/", "docs/", "frontend/public/", "frontend/src/")
FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".github",
        ".local",
        ".pytest_cache",
        "node_modules",
        "__pycache__",
        "dist",
        "coverage",
    }
)


def relative_name(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def is_allowed(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    name = relative_name(path)
    if name in ALLOWED_FILES or any(name.startswith(prefix) for prefix in ALLOWED_DIRECTORIES):
        return not any(part in FORBIDDEN_PARTS for part in Path(name).parts)
    return False


def collect_release_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if is_allowed(path):
            files.append(path)
    return sorted(files, key=relative_name)


def verify_archive(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        allowed_names = {relative_name(item) for item in collect_release_files()}
        invalid = [name for name in names if name not in allowed_names]
        invalid.extend(f"missing archive entry: {name}" for name in sorted(allowed_names - set(names)))
        if len(names) != len(set(names)):
            invalid.append("duplicate archive entry")
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            invalid.append("archive path traversal entry")
        return invalid


def create_archive(output: Path) -> int:
    files = collect_release_files()
    if not files:
        raise RuntimeError("release allowlist matched no files")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, relative_name(path))
    invalid = verify_archive(output)
    if invalid:
        output.unlink(missing_ok=True)
        raise RuntimeError("release archive verification failed: " + ", ".join(invalid))
    print(f"Created and verified {output} ({len(files)} files).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the allowlist without writing an archive")
    parser.add_argument("--output", type=Path, help="archive path; defaults to dist/medialogue-source.zip")
    args = parser.parse_args()
    files = collect_release_files()
    if not files:
        parser.error("release allowlist matched no files")
    if args.check:
        print(f"Release allowlist verified ({len(files)} files).")
        return 0
    return create_archive((args.output or ROOT / "dist" / "medialogue-source.zip").resolve())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        print(f"Release archive error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
