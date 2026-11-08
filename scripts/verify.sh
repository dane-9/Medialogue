#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python}"
RUN_DOCKER=0

usage() {
  cat <<'EOF'
Usage: ./scripts/verify.sh [--docker]

Runs Medialogue's manual verification suite:
  - backend dependency preflight
  - Python compilation
  - backend lint (ruff)
  - OpenAPI snapshot + generated TypeScript contract drift check
  - backend pytest suite
  - frontend dependency preflight
  - frontend Vitest suite
  - strict TypeScript + production Vite build

Options:
  --docker  Also validate Docker Compose configuration and build the production image.
  -h, --help  Show this help.

Environment:
  PYTHON=/path/to/python  Override the Python interpreter used for backend checks.
EOF
}

fail() {
  printf '\nVerification cannot continue: %s\n' "$1" >&2
  exit 1
}

section() {
  printf '\n==> %s\n' "$1"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command '$1' was not found."
}

while (($#)); do
  case "$1" in
    --docker)
      RUN_DOCKER=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "unknown argument: $1"
      ;;
  esac
  shift
done

cd "$ROOT"

section "Environment preflight"
require_command "$PYTHON_BIN"
require_command npm

"$PYTHON_BIN" - <<'PY' || fail "Medialogue requires Python 3.12 or newer. Set PYTHON=... to choose another interpreter."
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY

if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import aiosqlite
import fastapi
import httpx
import pydantic
import pytest
import sqlalchemy
PY
then
  fail "backend dependencies are missing. Install them with: $PYTHON_BIN -m pip install -e './backend[test]'"
fi

if [[ ! -d frontend/node_modules ]]; then
  fail "frontend dependencies are missing. Install them with: cd frontend && npm ci"
fi

section "Backend compile"
"$PYTHON_BIN" -m compileall -q backend/app backend/tests

section "Backend lint"
"$PYTHON_BIN" -m ruff --version >/dev/null 2>&1 || fail "ruff is missing. Install it with: $PYTHON_BIN -m pip install ruff==0.17.4"
(
  cd backend
  "$PYTHON_BIN" -m ruff check app tests
)

section "OpenAPI contract"
"$PYTHON_BIN" scripts/generate_api_contract.py --check

section "Backend tests"
(
  cd backend
  "$PYTHON_BIN" -m pytest -q
)

section "Frontend tests"
(
  cd frontend
  npm test
)

section "Frontend typecheck and production build"
(
  cd frontend
  npm run build
)

if ((RUN_DOCKER)); then
  section "Docker Compose validation"
  require_command docker
  docker compose config -q

  section "Production container build"
  docker compose build media-manager
fi

printf '\nAll requested Medialogue verification checks passed.\n'
