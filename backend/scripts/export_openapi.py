from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def render_openapi() -> str:
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Medialogue's FastAPI OpenAPI document.")
    parser.add_argument("output", type=Path, help="Path to write the OpenAPI JSON document.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the output file does not already match the current API contract.",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    rendered = render_openapi()

    if args.check:
        if not output.is_file():
            print(f"OpenAPI snapshot is missing: {output}", file=sys.stderr)
            return 1
        if output.read_text(encoding="utf-8") != rendered:
            print(
                "OpenAPI snapshot is stale. Run `python scripts/generate_api_contract.py` from the repository root.",
                file=sys.stderr,
            )
            return 1
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
