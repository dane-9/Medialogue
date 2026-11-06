# syntax=docker/dockerfile:1
FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS frontend-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="Medialogue" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.description="Leave-in-place media inventory and download manager"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIALOGUE_ENVIRONMENT=production \
    MEDIALOGUE_CONFIG_DIR=/config \
    MEDIALOGUE_TORRENT_ARCHIVE_DIR=/torrent-archive \
    MEDIALOGUE_DATABASE_URL=sqlite+aiosqlite:////config/medialogue.db
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gosu passwd \
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
RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps . \
    && python -c "import alembic; from alembic.config import main; assert getattr(alembic, '__version__', None); print('Alembic', alembic.__version__)" \
    && alembic --version \
    && test "$(alembic heads | grep -c '(head)')" -eq 1 \
    && alembic heads | grep -q '^0002 (head)$' \
    && test -x /usr/local/bin/medialogue-entrypoint \
    && mkdir -p /config /torrent-archive \
    && chown -R appuser:appuser /app /config /torrent-archive
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/readyz', timeout=3)" || exit 1
ENTRYPOINT ["/usr/local/bin/medialogue-entrypoint"]
