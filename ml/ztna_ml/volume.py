"""Peer-level volume rule: sustained exfiltration relative to the peer's own normal.

Slow data theft at a rate that is ordinary for *some* users is invisible in a single
window (and to models trained on the population). It shows up as cumulative volume over
minutes compared with what this particular peer normally moves.
"""

from __future__ import annotations

import os
from collections import OrderedDict, deque
from dataclasses import dataclass

from .rules import Detection

HORIZON_WINDOWS = 20  # 10 minutes of 30 s windows
WARMUP_WINDOWS = 10  # 5 minutes of history before the relative limit applies
BASELINE_ALPHA = 0.02  # half-life ~34 windows
CLIP_FACTOR = 4.0  # robust EWMA: one window can move the baseline at most this much


def _env(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class VolumeConfig:
    relative_factor: float = _env("RULE_SUSTAINED_EXFIL_FACTOR", 8)
    min_bytes: int = int(_env("RULE_SUSTAINED_EXFIL_MIN_BYTES", 200 * 1024 * 1024))
    cold_start_bytes: int = int(_env("RULE_SUSTAINED_EXFIL_COLD_BYTES", 500 * 1024 * 1024))
    penalty: float = 65.0


@dataclass
class _PeerVolume:
    recent: deque[int]
    baseline: float  # bytes per window
    windows: int = 0
    cooldown: int = 0


class VolumeTracker:
    def __init__(self, config: VolumeConfig | None = None, max_peers: int = 10_000):
        self.config = config or VolumeConfig()
        self.max_peers = max_peers
        self._peers: OrderedDict[str, _PeerVolume] = OrderedDict()

    def export_peer(self, peer: str) -> dict | None:
        st = self._peers.get(peer)
        if st is None:
            return None
        return {
            "recent": list(st.recent),
            "baseline": st.baseline,
            "windows": st.windows,
            "cooldown": st.cooldown,
        }

    def import_peer(self, peer: str, d: dict | None) -> None:
        if d:
            recent = deque(d["recent"], maxlen=HORIZON_WINDOWS)
            self._peers[peer] = _PeerVolume(recent, d["baseline"], d["windows"], d["cooldown"])

    def observe(self, peer: str, bytes_out: int) -> Detection | None:
        """Feed one closed window of a peer; returns a detection when the limit is crossed."""
        c = self.config
        st = self._peers.get(peer)
        if st is None:
            st = self._peers[peer] = _PeerVolume(deque(maxlen=HORIZON_WINDOWS), float(bytes_out))
            while len(self._peers) > self.max_peers:
                self._peers.popitem(last=False)
        self._peers.move_to_end(peer)

        st.recent.append(bytes_out)
        st.windows += 1
        total = sum(st.recent)
        if st.windows >= WARMUP_WINDOWS:
            limit = max(c.min_bytes, c.relative_factor * HORIZON_WINDOWS * st.baseline)
        else:
            limit = c.cold_start_bytes

        if st.windows <= WARMUP_WINDOWS:
            # Learn the peer's level as a plain mean first; clipping against a cold,
            # arbitrary first value would lock the baseline far too low.
            st.baseline += (bytes_out - st.baseline) / st.windows
        else:
            # Robust update: outlier windows are clipped and barely weighted, so a long
            # exfiltration cannot raise its own limit, while a lasting change of habits
            # is still learned over hours.
            cap = CLIP_FACTOR * max(st.baseline, 1.0)
            alpha = BASELINE_ALPHA if bytes_out <= cap else BASELINE_ALPHA / 10
            st.baseline = (1 - alpha) * st.baseline + alpha * min(bytes_out, cap)

        if st.cooldown:
            st.cooldown -= 1
            return None
        if total >= limit:
            st.cooldown = HORIZON_WINDOWS  # at most once per horizon
            return Detection(
                "rule",
                "sustained_exfiltration",
                c.penalty,
                {
                    "bytes_10min": total,
                    "limit": int(limit),
                    "baseline_per_window": int(st.baseline),
                },
            )
        return None
