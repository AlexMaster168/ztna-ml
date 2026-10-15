"""Fire-and-forget live notifications for the dashboard (Redis pub/sub)."""

import json
import logging
import time

import redis
from django.conf import settings

log = logging.getLogger(__name__)

CHANNEL = "ztna:live"
_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=1)
    return _client


def publish(kind: str, data: dict) -> None:
    if not getattr(settings, "LIVE_EVENTS", True):
        return
    try:
        _redis().publish(
            CHANNEL, json.dumps({"type": kind, "ts": time.time(), **data}, default=str)
        )
    except redis.RedisError as exc:  # live updates must never break a state change
        log.warning("live publish failed: %s", exc)


SUSPICIOUS = "ml:suspicious"


def mark_suspicious(peer_ip: str) -> None:
    """The trainer leaves a peer's traffic around each incident out of its "normal
    traffic" baseline (one "ip|ts" mark per incident)."""
    if not getattr(settings, "LIVE_EVENTS", True):
        return
    now = time.time()
    try:
        _redis().zadd(SUSPICIOUS, {f"{peer_ip}|{now:.0f}": now})
    except redis.RedisError as exc:
        log.warning("could not mark %s suspicious: %s", peer_ip, exc)
