# syntax=docker/dockerfile:1
FROM node:22-alpine AS frontend-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Keep pg_basebackup on the same PostgreSQL major as the supplied Compose DB.
FROM postgres:16-bookworm AS postgres-tools

FROM python:3.12-slim-bookworm AS runtime
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="Medialogue" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.description="Leave-in-place media inventory and download manager"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIALOGUE_ENVIRONMENT=production \
    MEDIALOGUE_CONFIG_DIR=/config \
    MEDIALOGUE_TORRENT_ARCHIVE_DIR=/torrent-archive \
    MEDIALOGUE_RECOVERY_EXPORT_DIR=/config/recovery-exports \
    MEDIALOGUE_PG_BASEBACKUP_BIN=/usr/lib/postgresql/16/bin/pg_basebackup
WORKDIR /app
COPY --from=postgres-tools /usr/lib/postgresql/16/ /usr/lib/postgresql/16/
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gosu libpq5 liblz4-1 libzstd1 passwd \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 appuser \
    && useradd --create-home --uid 10001 --gid 10001 appuser
COPY backend/ /app/backend/
COPY --from=frontend-build /src/frontend/dist/ /app/frontend/dist/
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/medialogue-entrypoint
WORKDIR /app/backend
# backend/alembic/ is Medialogue's migration script directory. Setuptools
# must not install it as the Python "alembic" package because that would
# collide with the real Alembic dependency. Verify the import/CLI in-image.
RUN pip install --no-cache-dir . \
    && python -c "import alembic; from alembic.config import main; assert getattr(alembic, '__version__', None); print('Alembic', alembic.__version__)" \
    && alembic --version \
    && test -x /usr/local/bin/medialogue-entrypoint \
    && mkdir -p /config/recovery-exports /torrent-archive \
    && chown -R appuser:appuser /app /config /torrent-archive
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/readyz', timeout=3)" || exit 1
ENTRYPOINT ["/usr/local/bin/medialogue-entrypoint"]
