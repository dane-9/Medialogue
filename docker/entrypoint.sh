#!/bin/sh
set -eu

PUID="${PUID:-10001}"
PGID="${PGID:-10001}"

run_app() {
  cd /app/backend
  alembic upgrade head
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
}

# Internal re-entry target used when gosu keeps UID 0 but changes the primary
# group (for example PUID=0, PGID=1000 on TrueNAS). This must be checked
# before the root setup block or UID 0 would recurse forever.
if [ "${1:-}" = "--run-app" ]; then
  shift
  run_app
fi

# Bind-mounted application-owned directories are prepared before privileges are
# dropped. Media roots are deliberately never chowned or modified here.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /config/recovery-exports /torrent-archive

  # TrueNAS/qBittorrent-style deployments commonly use PUID=0. Do not mutate
  # appuser to UID 0 and then re-exec the entrypoint: that would recurse forever.
  # Instead, keep UID 0 and optionally adopt the requested primary GID.
  if [ "$PUID" = "0" ]; then
    chown 0:"$PGID" /config /torrent-archive /config/recovery-exports
    exec gosu "0:$PGID" "$0" --run-app "$@"
  fi

  current_gid="$(id -g appuser)"
  current_uid="$(id -u appuser)"
  identity_changed=0
  if [ "$current_gid" != "$PGID" ]; then groupmod -o -g "$PGID" appuser; identity_changed=1; fi
  if [ "$current_uid" != "$PUID" ]; then usermod -o -u "$PUID" appuser; identity_changed=1; fi

  # Normal restarts only need the mount roots prepared. If the configured
  # identity changed, migrate ownership of Medialogue-owned data once. Media
  # mounts are deliberately never touched here.
  if [ "$identity_changed" = "1" ]; then
    chown -R "$PUID:$PGID" /config /torrent-archive
  else
    chown "$PUID:$PGID" /config /torrent-archive /config/recovery-exports
  fi
  exec gosu appuser "$0" "$@"
fi

run_app
