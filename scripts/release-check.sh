#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

python -m compileall -q backend/app backend/tests
(
  cd backend
  pytest -q --ignore=tests/test_postgres_integration.py
)

if command -v npm >/dev/null 2>&1; then
  if [ -d frontend/node_modules ]; then
    (cd frontend && npm test && npm run build)
  else
    echo "frontend/node_modules is absent; run 'cd frontend && npm ci' before the frontend release gates." >&2
  fi
fi

echo "Release source checks completed."
