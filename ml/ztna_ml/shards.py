"""Partitioning of gateway traffic into shard streams.

All requests of one peer land in the same shard, so a single worker owns a peer's whole
state (windows, baselines, history). The log shippers use the same function; keep
services/log-shipper/shipper.py:shard_of in sync with this one.
"""

from __future__ import annotations

import os
import zlib

SHARDS = int(os.environ.get("TRAFFIC_SHARDS", "8"))
LEGACY_STREAM = "traffic:http"  # single stream used before sharding


def shard_of(gw: str, peer_ip: str, shards: int = SHARDS) -> int:
    return zlib.crc32(f"{gw}|{peer_ip}".encode()) % shards


def stream_name(shard: int) -> str:
    return f"traffic:http:{shard}"


def all_streams(shards: int = SHARDS) -> list[str]:
    return [stream_name(s) for s in range(shards)]
