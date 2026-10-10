"""Long-running consumer of events:anomaly plus the periodic decay/release tick."""

import logging
import socket
import time
from pathlib import Path

import redis
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from trust import engine

log = logging.getLogger("trust-engine")

STREAM = "events:anomaly"
GROUP = "trust-engine"
TICK_SECONDS = 5
HEARTBEAT = Path("/tmp/heartbeat")


class Command(BaseCommand):
    help = "Consume anomaly events, maintain trust scores and enforce policy."

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO, format="[trust-engine] %(levelname)s %(message)s")
        consumer = socket.gethostname()
        while True:
            try:
                self._run(redis.Redis.from_url(settings.REDIS_URL), consumer)
            except redis.ConnectionError as exc:
                log.warning("redis connection lost (%s); reconnecting", exc)
                time.sleep(2)

    def _run(self, r: redis.Redis, consumer: str) -> None:
        try:
            r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        # First drain our own pending entries (crash recovery), then new ones.
        cursor = "0"
        last_tick = 0.0
        while True:
            resp = r.xreadgroup(GROUP, consumer, {STREAM: cursor}, count=100, block=1000)
            entries = resp[0][1] if resp else []
            if cursor == "0" and not entries:
                cursor = ">"
            for entry_id, fields in entries:
                record = {k.decode(): v.decode() for k, v in fields.items()}
                close_old_connections()
                try:
                    engine.handle_event(record)
                except Exception:
                    log.exception("failed to handle event %s", entry_id)
                r.xack(STREAM, GROUP, entry_id)
            if time.monotonic() - last_tick >= TICK_SECONDS:
                HEARTBEAT.touch()
                close_old_connections()
                engine.tick()
                last_tick = time.monotonic()
