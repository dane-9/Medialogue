# Upgrade

Medialogue runs Alembic database migrations automatically when the application container starts.

## Recommended upgrade procedure

1. Create a **Recovery Bundle** from Settings → Backup / Recovery and keep it somewhere outside the Medialogue host.
2. Stop active destructive work and allow running Jobs to finish when practical.
3. Pull or replace the application source/image for the target release.
4. Rebuild and restart:

   ```sh
   docker compose pull        # when using a published image
   docker compose up -d --build
   ```

5. Watch startup logs:

   ```sh
   docker compose logs -f media-manager
   ```

6. Confirm readiness:

   ```sh
   curl -fsS http://HOST:8000/readyz
   ```

7. Sign in, verify Integration Health, and initialize any newly added Storage Root with one explicit scan.

## Migration behavior

The entrypoint executes:

```sh
alembic upgrade head
```

before starting the API. If a migration fails, Uvicorn is not started and the container remains unhealthy/restarts according to Compose policy. Do not bypass a failed migration by manually stamping the database unless you have inspected the schema and understand the consequence.

## PostgreSQL major version

The supplied stack uses PostgreSQL 16. Recovery Bundles use a physical PostgreSQL base backup, so restore that backup with the same PostgreSQL major version recorded in `backup-metadata.json`.

## Downgrades

Automatic database downgrade is not a supported recovery strategy. Restore a Recovery Bundle made before the upgrade instead.
