"""Parsing of gateway access-log records (nginx `ztna_json` format)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Request:
    ts: float
    peer_ip: str
    method: str
    path: str
    status: int
    bytes_sent: int
    request_length: int
    rt: float
    ua: str
    # Refused by the gateway's access policy (403 from nginx, never reached the service).
    denied: bool = False


def parse_line(raw: bytes | str) -> Request | None:
    """Return a Request, or None for malformed records (never raises)."""
    try:
        d = json.loads(raw)
        uri = d.get("uri") or "/"
        return Request(
            ts=datetime.fromisoformat(d["ts"]).timestamp(),
            peer_ip=d["peer_ip"],
            method=d.get("method", ""),
            path=uri.split("?", 1)[0],
            status=int(d["status"]),
            bytes_sent=int(d.get("bytes_sent", 0)),
            request_length=int(d.get("request_length", 0)),
            rt=float(d.get("rt", 0) or 0),
            ua=d.get("ua", "") or "",
            denied=d.get("allow") == "0" and bool(d.get("resource")),
        )
    except (ValueError, KeyError, TypeError):
        return None
