"""Offline dataset generation: plays the simulator's behaviour profiles against an emulated
protected-app in simulated time and turns the resulting access log into labelled windows.

The profiles are the same code the live peers run (simulator/peer/traffic/profiles.py).
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .events import Request
from .features import WINDOW_SECONDS, WindowManager
from .rules import RuleConfig, evaluate
from .volume import VolumeTracker

_PROFILES_DIR = Path(__file__).resolve().parents[2] / "simulator" / "peer" / "traffic"
if str(_PROFILES_DIR) not in sys.path:
    sys.path.insert(0, str(_PROFILES_DIR))

import profiles  # noqa: E402

PAGE_BYTES = 1_000
INDEX_BYTES = 404
ITEMS_BYTES = 900
ERROR_BYTES = 180
THROUGHPUT = 150 * 1024 * 1024  # bytes/s for download timing


class EmulatedApp:
    """Responds like services/protected-app behind nginx (status, size, latency)."""

    def respond(self, a: profiles.Action, rng: random.Random) -> tuple[int, int, float]:
        path = a.path
        if a.method == "POST" and path == "/login":
            ok = a.form == profiles.LOGIN_OK
            return (200, 160, 0.004) if ok else (401, 190, 0.003)
        if path == "/":
            return 200, INDEX_BYTES, 0.003
        if path.startswith("/pages/"):
            if path.removeprefix("/pages/") in profiles.PAGES:
                return 200, PAGE_BYTES + rng.randint(-50, 50), 0.004
            return 404, ERROR_BYTES, 0.002
        if path == "/api/items":
            return 200, ITEMS_BYTES, 0.003
        if path.startswith("/files/"):
            try:
                kb = max(1, min(int(path.removeprefix("/files/")), 50_000))
            except ValueError:
                return 422, ERROR_BYTES, 0.002
            size = kb * 1024 + 220
            return 200, size, 0.002 + size / THROUGHPUT
        return 404, ERROR_BYTES, 0.002


def request_length(a: profiles.Action) -> int:
    base = 95 + len(a.path) + len(a.ua)
    if a.form:
        body = sum(len(k) + len(v) + 2 for k, v in a.form.items())
        base += 80 + body
    return base


def play(profile: str, peer_ip: str, start: float, duration: float, seed: int) -> list[Request]:
    rng = random.Random(seed)
    app = EmulatedApp()
    out: list[Request] = []
    t = start
    for a in profiles.PROFILES[profile](rng):
        if t >= start + duration:
            break
        status, size, rt = app.respond(a, rng)
        rt *= rng.lognormvariate(0, 0.3)
        out.append(Request(t, peer_ip, a.method, a.path, status, size, request_length(a), rt, a.ua))
        t += rt + a.sleep
    return out


@dataclass
class Segment:
    profile: str
    start: float
    duration: float
    label: str  # "normal" or the attack name


@dataclass
class Dataset:
    X: np.ndarray  # (n, n_features)
    labels: np.ndarray  # (n,) str
    peers: np.ndarray  # (n,) str
    starts: np.ndarray  # (n,) window start
    rule_hit: np.ndarray  # (n,) bool, any deterministic rule fired in the window
    counts: np.ndarray  # (n,) requests in the window

    def subset(self, mask: np.ndarray) -> Dataset:
        return Dataset(
            self.X[mask],
            self.labels[mask],
            self.peers[mask],
            self.starts[mask],
            self.rule_hit[mask],
            self.counts[mask],
        )


def windows_from(segments_by_peer: dict[str, list[Segment]], seed: int) -> Dataset:
    """Simulate each peer's segments and cut the traffic into labelled windows.
    A window takes the label of the segment that produced its requests; windows that mix
    segments are dropped so labels stay clean."""
    reqs: list[tuple[Request, str]] = []
    for i, (ip, segs) in enumerate(sorted(segments_by_peer.items())):
        for j, s in enumerate(segs):
            for r in play(s.profile, ip, s.start, s.duration, seed * 10_007 + i * 101 + j):
                reqs.append((r, s.label))
    reqs.sort(key=lambda x: x[0].ts)

    m = WindowManager()
    label_of: dict[tuple[str, float], set[str]] = {}
    rows, labels, peers, starts, rule_hit, counts = [], [], [], [], [], []
    rule_cfg = RuleConfig()
    volume = VolumeTracker()

    def collect(windows):
        for w in windows:
            peer_rule = volume.observe(w.peer_ip, w.bytes_sum) is not None
            ls = label_of.pop((w.peer_ip, w.start), set())
            if len(ls) == 1:
                rows.append(w.vector())
                labels.append(next(iter(ls)))
                peers.append(w.peer_ip)
                starts.append(w.start)
                rule_hit.append(bool(evaluate(w, rule_cfg)) or peer_rule)
                counts.append(w.count)

    for r, label in reqs:
        w, closed = m.add(r)
        collect(closed)
        label_of.setdefault((w.peer_ip, w.start), set()).add(label)
    collect(m.flush())
    return Dataset(
        np.array(rows, dtype=np.float32),
        np.array(labels),
        np.array(peers),
        np.array(starts),
        np.array(rule_hit),
        np.array(counts),
    )


def build(
    seed: int,
    normal_peers: int = 16,
    hours: float = 3.0,
    attack_peers: int = 6,
    attack_minutes: float = 10.0,
    attacks: list[str] | None = None,
) -> Dataset:
    """Normal population plus attackers that first behave normally, then attack."""
    rng = random.Random(seed)
    attacks = attacks or list(profiles.ATTACKS)
    t0 = 1_800_000_000.0  # window aligned
    segs: dict[str, list[Segment]] = {}
    ip = 2
    for _ in range(normal_peers):
        prof = "developer" if rng.random() < 0.25 else "browser"
        offset = rng.uniform(0, 600)
        segs[f"10.13.{ip // 250}.{ip % 250}"] = [
            Segment(prof, t0 + offset, hours * 3600 - offset, "normal")
        ]
        ip += 1
    for attack in attacks:
        for _ in range(attack_peers):
            warmup = math.ceil(rng.uniform(5, 20) * 60 / WINDOW_SECONDS) * WINDOW_SECONDS
            start = (
                t0
                + math.ceil(rng.uniform(0, hours * 3600 - 3600) / WINDOW_SECONDS) * WINDOW_SECONDS
            )
            segs[f"10.13.{ip // 250}.{ip % 250}"] = [
                Segment("browser", start, warmup, "normal"),
                Segment(attack, start + warmup, attack_minutes * 60, attack),
            ]
            ip += 1
    return windows_from(segs, seed)
