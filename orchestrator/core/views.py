import redis
from django.conf import settings
from django.db import connection
from django.http import JsonResponse


def healthz(request):
    checks = {}
    try:
        connection.ensure_connection()
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report any backend failure
        checks["db"] = f"error: {exc.__class__.__name__}"
    try:
        redis.Redis.from_url(settings.REDIS_URL, socket_timeout=1).ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc.__class__.__name__}"
    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", **checks}, status=200 if healthy else 503
    )
