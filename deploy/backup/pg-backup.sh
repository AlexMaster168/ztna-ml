#!/bin/sh
# Periodic logical backups with retention. Restore:
#   pg_restore -h postgres -U "$PGUSER" -d "$PGDATABASE" --clean /backups/<file>.dump
set -eu
INTERVAL=${BACKUP_INTERVAL_SECONDS:-21600}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-7}
until pg_isready -q; do sleep 2; done
while true; do
  f="/backups/ztna-$(date -u +%Y%m%dT%H%M%SZ).dump"
  if pg_dump -Fc -f "$f.tmp"; then
    mv "$f.tmp" "$f"
    echo "[pg-backup] wrote $f ($(du -h "$f" | cut -f1))"
  else
    rm -f "$f.tmp"
    echo "[pg-backup] backup failed" >&2
  fi
  find /backups -name 'ztna-*.dump' -mtime +"$KEEP_DAYS" -delete
  sleep "$INTERVAL"
done
