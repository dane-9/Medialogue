# v4 Startup Packaging Fix

This release fixes the startup failure:

```text
ImportError: cannot import name '__version__' from 'alembic'
```

## Cause

Older Medialogue packaging allowed `backend/alembic/` (the migration-script directory) to be discovered as a Python package. If an old `backend/alembic/__init__.py` remained in a GitHub repository, the Medialogue wheel could claim the same `alembic` package name as the real third-party Alembic dependency.

GitHub's web uploader adds/replaces uploaded files but does not remove obsolete files from previous uploads automatically.

## Fixes in v4

- setuptools packages only `app*`;
- migration scripts and tests are excluded from the installed Python distribution;
- Docker build verifies `import alembic`, `alembic.__version__`, and `alembic --version`;
- manual GHCR publishing validates the built image before logging in/pushing;
- manual CI validates the packaged runtime before booting the stack.

## Upgrade steps

1. Replace your repository contents with this v4 source.
2. If `backend/alembic/__init__.py` still exists in GitHub, delete it. v4 is safe even if it remains, but removing the obsolete file keeps the repository correct.
3. Run **Actions → CI (manual) → Run workflow**.
4. When it is green, run **Actions → Publish GHCR Image (manual)** and type `PUBLISH`.
5. In Dockge pull/recreate Medialogue; do not merely restart the existing container:

```sh
docker compose pull medialogue
docker compose up -d --force-recreate medialogue
```

6. Keep the port mapping as host 8010 → container 8000 if that is your chosen external port:

```yaml
ports:
  - "8010:8000"
```

7. Inside the Medialogue container the health endpoints remain on port 8000:

```sh
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```
