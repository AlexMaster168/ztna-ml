"""Long-running consumer of events:anomaly plus the periodic decay/release tick.

Also the orchestrator's side of continuous training: it registers models produced by
the trainer (ml:train:results), mirrors the training policy to Redis and promotes the
shadow challenger when its live results allow it.
"""

import hashlib
import logging
import socket
import time
from pathlib import Path

import redis
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core import partitions
from mlops import services as mlops
from mlops.models import MLOpsSettings
from trust import engine

log = logging.getLogger("trust-engine")

STREAM = "events:anomaly"
GROUP = "trust-engine"
TICK_SECONDS = 5
MLOPS_SECONDS = 60
PARTITION_SECONDS = 3600
DEDUPE_TTL = 6 * 3600
HEARTBEAT = Path("/tmp/heartbeat")


def dedupe_key(record: dict[str, str]) -> str:
    """ML workers deliver at least once (a shard that moves replays its uncheckpointed
    tail), so the same detection can arrive twice; this identifies it."""
    ident = "|".join(record.get(k, "") for k in ("gw", "peer_ip", "source", "kind", "window_start"))
    return "events:seen:" + hashlib.sha1(ident.encode()).hexdigest()


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

    @staticmethod
    def _group(r: redis.Redis, stream: str) -> None:
        try:
            r.xgroup_create(stream, GROUP, id="$", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _run(self, r: redis.Redis, consumer: str) -> None:
        self._group(r, STREAM)
        self._group(r, mlops.TRAIN_RESULTS)
        # First drain our own pending entries (crash recovery), then new ones.
        cursor = "0"
        last_tick = last_mlops = last_partitions = 0.0
        while True:
            resp = r.xreadgroup(
                GROUP,
                consumer,
                {STREAM: cursor, mlops.TRAIN_RESULTS: cursor},
                count=100,
                block=1000,
            )
            got = False
            for stream, entries in resp or []:
                got = got or bool(entries)
                for entry_id, fields in entries:
                    record = {k.decode(): v.decode() for k, v in fields.items()}
                    close_old_connections()
                    try:
                        if stream.decode() == STREAM:
                            if r.set(dedupe_key(record), 1, nx=True, ex=DEDUPE_TTL):
                                engine.handle_event(record)
                        else:
                            mlops.handle_training_result(record)
                    except Exception:
                        log.exception("failed to handle %s %s", stream, entry_id)
                    r.xack(stream, GROUP, entry_id)
            if cursor == "0" and not got:
                cursor = ">"
            if time.monotonic() - last_tick >= TICK_SECONDS:
                HEARTBEAT.touch()
                close_old_connections()
                engine.tick()
                last_tick = time.monotonic()
            if time.monotonic() - last_mlops >= MLOPS_SECONDS:
                close_old_connections()
                try:
                    mlops.push_trainer_config(MLOpsSettings.get())
                    mlops.maybe_promote()
                except Exception:
                    log.exception("mlops housekeeping failed")
                last_mlops = time.monotonic()
            if time.monotonic() - last_partitions >= PARTITION_SECONDS:
                close_old_connections()
                try:
                    if report := partitions.maintain():
                        log.info("partitions: %s", report)
                except Exception:
                    log.exception("partition maintenance failed")
                last_partitions = time.monotonic()
