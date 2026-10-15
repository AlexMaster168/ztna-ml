"""Real gateway traffic from the Redis stream: window collection for training on the
network's own baseline, and a sim-to-real check of a model.

    python -m ztna_ml.livecheck --model /models/<version> --attacker 10.13.0.3 [--minutes 60]

Windows from attacker IPs are labelled "attack", all others "normal". The point is the
false-positive rate on real normal traffic, which synthetic evaluation cannot show.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import redis

from .events import parse_line
from .features import Window, WindowManager
from .model import OnnxAutoencoder
from .rules import RuleConfig, evaluate
from .shards import LEGACY_STREAM, all_streams
from .worker import MIN_REQUESTS_FOR_MODEL


@dataclass
class LiveWindow:
    window: Window
    rule_hit: bool


@dataclass(frozen=True)
class Span:
    """A peer's traffic between two unix timestamps (e.g. around an incident)."""

    ip: str
    start: float
    end: float

    @classmethod
    def parse(cls, s: str) -> Span:
        ip, start, end = s.split(",")
        return cls(ip, float(start), float(end))

    def __str__(self) -> str:
        return f"{self.ip},{self.start:.0f},{self.end:.0f}"


def in_spans(w: Window, spans: list[Span]) -> bool:
    return any(s.ip == w.peer_ip and w.start < s.end and w.end > s.start for s in spans)


def _read(r: redis.Redis, stream: str, start: float, end: float) -> list[tuple[int, int, bytes]]:
    out = []
    cursor = f"{int(start * 1000)}-0"
    end_id = f"{int(end * 1000)}-0"
    while True:
        batch = r.xrange(stream, min=cursor, max=end_id, count=5000)
        if not batch:
            break
        for entry_id, fields in batch:
            ms, seq = entry_id.decode().split("-")
            out.append((int(ms), int(seq), fields[b"data"]))
        ms, seq = batch[-1][0].decode().split("-")
        cursor = f"{ms}-{int(seq) + 1}"
    return out


def collect_windows(r: redis.Redis, start: float, end: float) -> list[LiveWindow]:
    """Rebuild tumbling windows from the traffic streams (all shards, merged in arrival
    order) between two unix timestamps."""
    entries = []
    for stream in [LEGACY_STREAM, *all_streams()]:
        entries += _read(r, stream, start, end)
    entries.sort(key=lambda e: (e[0], e[1]))
    m = WindowManager()
    closed: list[Window] = []
    for _ms, _seq, data in entries:
        req = parse_line(data)
        if req:
            closed += m.add(req)[1]
    closed += m.flush()
    cfg = RuleConfig()
    return [LiveWindow(w, bool(evaluate(w, cfg))) for w in closed]


def score_report(
    scorer: OnnxAutoencoder,
    windows: list[LiveWindow],
    attackers: set[str],
    spans: list[Span] | None = None,
) -> dict:
    """Windows of `attackers`, or inside one of `spans`, count as attack traffic."""
    rows = []
    history: dict[str, list[np.ndarray]] = {}
    for lw in sorted(windows, key=lambda lw: lw.window.start):
        w = lw.window
        h = history.setdefault(w.peer_ip, [])
        h.append(w.vector())
        if w.count >= MIN_REQUESTS_FOR_MODEL and len(h) >= scorer.history_needed:
            attack = w.peer_ip in attackers or in_spans(w, spans or [])
            rows.append((attack, scorer.score(h), w, list(h)))
    normal = np.array([s for a, s, _, _ in rows if not a])
    attack = np.array([s for a, s, _, _ in rows if a])
    return {
        "model": scorer.version,
        "normal_windows": int(len(normal)),
        "normal_fpr": round(float((normal >= 1).mean()), 4) if len(normal) else None,
        "normal_ratio_p50": round(float(np.median(normal)), 3) if len(normal) else None,
        "normal_ratio_p99": round(float(np.quantile(normal, 0.99)), 3) if len(normal) else None,
        "attack_windows": int(len(attack)),
        "attack_recall": round(float((attack >= 1).mean()), 4) if len(attack) else None,
        "false_positives": [
            {
                "peer": w.peer_ip,
                "ratio": round(s, 2),
                "top": scorer.explain(h),
                **w.summary(),
            }
            for a, s, w, h in rows
            if not a and s >= 1
        ][:10],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--attacker", action="append", default=[])
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--redis", default=os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    args = ap.parse_args()

    r = redis.Redis.from_url(args.redis)
    now = time.time()
    windows = collect_windows(r, now - args.minutes * 60, now)
    scorer = OnnxAutoencoder(Path(args.model))
    print(json.dumps(score_report(scorer, windows, set(args.attacker)), indent=2, default=str))


if __name__ == "__main__":
    main()
