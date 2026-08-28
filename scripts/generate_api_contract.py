from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

OPENAPI_TYPESCRIPT_VERSION = "7.13.0"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPOSITORY_ROOT / "openapi" / "openapi.json"
GENERATED_TYPES_PATH = REPOSITORY_ROOT / "frontend" / "src" / "api" / "generated" / "schema.ts"
MANIFEST_PATH = REPOSITORY_ROOT / "openapi" / "contract-manifest.json"


def run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_types() -> None:
    npx = shutil.which("npx")
    if npx is None:
        raise SystemExit("npx is required to generate frontend OpenAPI types.")

    GENERATED_TYPES_PATH.parent.mkdir(parents=True, exist_ok=True)
    run(
        npx,
        "--yes",
        f"openapi-typescript@{OPENAPI_TYPESCRIPT_VERSION}",
        str(OPENAPI_PATH),
        "--output",
        str(GENERATED_TYPES_PATH),
        cwd=REPOSITORY_ROOT / "frontend",
    )


def write_manifest() -> None:
    manifest = {
        "generator": f"openapi-typescript@{OPENAPI_TYPESCRIPT_VERSION}",
        "openapi_sha256": sha256(OPENAPI_PATH),
        "typescript_sha256": sha256(GENERATED_TYPES_PATH),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_manifest() -> int:
    if not GENERATED_TYPES_PATH.is_file():
        print(f"Generated TypeScript contract is missing: {GENERATED_TYPES_PATH}", file=sys.stderr)
        return 1
    if not MANIFEST_PATH.is_file():
        print(
            "OpenAPI contract manifest is missing. Run `python scripts/generate_api_contract.py` from the repository root.",
            file=sys.stderr,
        )
        return 1

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"OpenAPI contract manifest is invalid: {exc}", file=sys.stderr)
        return 1

    expected = {
        "generator": f"openapi-typescript@{OPENAPI_TYPESCRIPT_VERSION}",
        "openapi_sha256": sha256(OPENAPI_PATH),
        "typescript_sha256": sha256(GENERATED_TYPES_PATH),
    }
    if manifest != expected:
        print(
            "Generated frontend API contract is stale or was edited manually. "
            "Run `python scripts/generate_api_contract.py` from the repository root.",
            file=sys.stderr,
        )
        return 1
    return 0


def check_contract() -> int:
    run(
        sys.executable,
        str(REPOSITORY_ROOT / "backend" / "scripts" / "export_openapi.py"),
        str(OPENAPI_PATH),
        "--check",
    )
    if check_manifest() != 0:
        return 1
    print("OpenAPI snapshot and generated TypeScript contract are current.")
    return 0


def generate_contract() -> int:
    run(
        sys.executable,
        str(REPOSITORY_ROOT / "backend" / "scripts" / "export_openapi.py"),
        str(OPENAPI_PATH),
    )
    generate_types()
    write_manifest()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify Medialogue's OpenAPI frontend contract.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the OpenAPI snapshot and generated TypeScript contract without changing files.",
    )
    args = parser.parse_args()
    return check_contract() if args.check else generate_contract()


if __name__ == "__main__":
    raise SystemExit(main())
