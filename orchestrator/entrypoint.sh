#!/usr/bin/env sh
set -e
if [ "${SKIP_MIGRATE:-0}" != "1" ]; then
  python manage.py migrate --noinput
  python manage.py bootstrap
fi
exec "$@"
