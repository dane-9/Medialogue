#!/bin/sh
set -eu

PUID="${PUID:-10001}"
PGID="${PGID:-10001}"

# Bind-mounted application-owned directories are prepared before privileges are
# dropped. Media roots are deliberately never chowned or modified here.
if [ "$(id -u)" = "0" ]; then
  current_gid="$(id -g appuser)"
  current_uid="$(id -u appuser)"
  identity_changed=0
  if [ "$current_gid" != "$PGID" ]; then groupmod -o -g "$PGID" appuser; identity_changed=1; fi
  if [ "$current_uid" != "$PUID" ]; then usermod -o -u "$PUID" appuser; identity_changed=1; fi
  mkdir -p /config/recovery-exports /torrent-archive
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

cd /app/backend
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
