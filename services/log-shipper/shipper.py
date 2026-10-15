"""Tails the gateway's nginx JSON access log into a Redis Stream.

At-least-once: the file offset is checkpointed only after a batch was written to Redis.
Handles rotation (rename + reopen): the old file is drained before switching.
"""

from __future__ import annotations

import json
import logging
import os
import time
import zlib
from pathlib import Path

import redis

log = logging.getLogger("log-shipper")

LOG_PATH = Path(os.environ.get("LOG_PATH", "/var/log/nginx/access.json"))
STATE_PATH = Path(os.environ.get("STATE_PATH", "/state/offset.json"))
STREAM_PREFIX = os.environ.get("STREAM_PREFIX", "traffic:http")
SHARDS = int(os.environ.get("TRAFFIC_SHARDS", "8"))
GATEWAY = os.environ.get("GATEWAY_NAME", "gw-1")
MAXLEN = int(os.environ.get("STREAM_MAXLEN", "1000000"))
BATCH = int(os.environ.get("BATCH_SIZE", "500"))
IDLE_SLEEP = 0.1

HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))


def heartbeat() -> None:
    """Touched every loop; the container healthcheck fails when it goes stale."""
    try:
        HEARTBEAT.touch()
    except OSError:
        pass


def load_state() -> tuple[int, int]:
    try:
        s = json.loads(STATE_PATH.read_text())
        return int(s["inode"]), int(s["offset"])
    except (OSError, ValueError, KeyError):
        return 0, 0


def save_state(inode: int, offset: int) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"inode": inode, "offset": offset}))
    tmp.replace(STATE_PATH)


def shard_of(gw: str, peer_ip: str, shards: int = SHARDS) -> int:
    """Must match ztna_ml.shards.shard_of: one peer, one shard, one worker."""
    return zlib.crc32(f"{gw}|{peer_ip}".encode()) % shards


def stream_for(line: bytes) -> str:
    try:
        peer = json.loads(line).get("peer_ip", "")
    except (ValueError, AttributeError):
        peer = ""
    return f"{STREAM_PREFIX}:{shard_of(GATEWAY, peer)}"


def ship(r: redis.Redis, lines: list[bytes]) -> None:
    pipe = r.pipeline(transaction=False)
    maxlen = max(MAXLEN // SHARDS, 1000)
    for line in lines:
        pipe.xadd(stream_for(line), {"gw": GATEWAY, "data": line}, maxlen=maxlen, approximate=True)
    pipe.execute()


class Tailer:
    def __init__(self, path: Path):
        self.path = path
        self.fh = None
        self.inode = 0
        self.buf = b""

    def open(self, inode: int, offset: int) -> bool:
        try:
            fh = self.path.open("rb")
        except FileNotFoundError:
            return False
        st = os.fstat(fh.fileno())
        # Resume only if it is the same file and it has not been truncated.
        start = offset if st.st_ino == inode and st.st_size >= offset else 0
        fh.seek(start)
        self.fh, self.inode, self.buf = fh, st.st_ino, b""
        log.info("tailing %s (inode %s) from offset %s", self.path, self.inode, start)
        return True

    @property
    def offset(self) -> int:
        """Offset of the first byte not yet shipped (excludes a buffered partial line)."""
        return self.fh.tell() - len(self.buf)

    def read_lines(self, limit: int) -> list[bytes]:
        lines: list[bytes] = []
        while len(lines) < limit:
            chunk = self.fh.readline()
            if not chunk:
                break
            self.buf += chunk
            if self.buf.endswith(b"\n"):
                line = self.buf.strip()
                self.buf = b""
                if line:
                    lines.append(line)
        return lines

    def rotated(self) -> bool:
        try:
            return os.stat(self.path).st_ino != self.inode
        except FileNotFoundError:
            return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[log-shipper] %(levelname)s %(message)s")
    r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tail = Tailer(LOG_PATH)
    inode, offset = load_state()
    while not tail.open(inode, offset):
        time.sleep(1)

    shipped = 0
    while True:
        heartbeat()
        lines = tail.read_lines(BATCH)
        if lines:
            while True:
                try:
                    ship(r, lines)
                    break
                except redis.RedisError as exc:
                    log.warning("redis unavailable (%s), retrying", exc)
                    time.sleep(2)
            save_state(tail.inode, tail.offset)
            shipped += len(lines)
            if shipped >= 10_000:
                log.info("shipped %s lines", shipped)
                shipped = 0
            continue
        if tail.rotated():
            # Old file fully drained above; switch to the new one from the start.
            tail.fh.close()
            while not tail.open(0, 0):
                time.sleep(1)
            save_state(tail.inode, 0)
            continue
        time.sleep(IDLE_SLEEP)


if __name__ == "__main__":
    main()
