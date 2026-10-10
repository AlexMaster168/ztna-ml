"""Per-peer tumbling-window aggregation and the feature vector fed to the models.

The same code runs online (worker) and offline (dataset building / training), so the
model sees exactly the features it was trained on.
"""

from __future__ import annotations

import math
import re
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from .events import Request

WINDOW_SECONDS = 30
GRACE_SECONDS = 5

AUTH_PATH = re.compile(r"^/(login|signin|auth|api/(auth|token|login))\b", re.I)
SCANNER_UA = re.compile(
    r"sqlmap|nikto|nmap|masscan|zgrab|gobuster|dirbuster|\bdirb\b|wfuzz|ffuf|hydra|nuclei|"
    r"acunetix|nessus|openvas|wpscan|feroxbuster|whatweb|metasploit|burp",
    re.I,
)

FEATURES = [
    "log_req_count",
    "uniq_path_ratio",
    "ratio_4xx",
    "ratio_auth_fail",
    "ratio_404",
    "ratio_5xx",
    "log_bytes_out_sum",
    "log_bytes_out_max",
    "mean_log_req_len",
    "post_ratio",
    "log_distinct_ua",
    "scanner_ua_ratio",
    "novel_ua_ratio",
    "interarrival_cv",
    # Sustained deviation from this peer's own history: log(short-term mean / long-term
    # mean) of bytes out and requests per window. 0 until the peer has history.
    "bytes_drift",
    "rate_drift",
]

FAST_ALPHA = 0.3  # ~3 windows: averages out single heavy windows
SLOW_ALPHA = 0.02  # half-life ~34 windows (17 minutes): the peer's normal


@dataclass
class Window:
    peer_ip: str
    start: float
    count: int = 0
    paths: set[str] = field(default_factory=set)
    n_4xx: int = 0
    n_404: int = 0
    n_5xx: int = 0
    n_auth_fail: int = 0
    n_post: int = 0
    n_scanner_ua: int = 0
    n_novel_ua: int = 0
    n_empty_ua: int = 0
    bytes_sum: int = 0
    bytes_max: int = 0
    req_len_log_sum: float = 0.0
    uas: set[str] = field(default_factory=set)
    timestamps: list[float] = field(default_factory=list)
    fired: set[str] = field(default_factory=set)  # rules already fired in this window
    # Set when the window closes (see PeerBaselines).
    drift_values: tuple[float, float] = (0.0, 0.0)

    @property
    def end(self) -> float:
        return self.start + WINDOW_SECONDS

    def add(self, r: Request, novel_ua: bool) -> None:
        self.count += 1
        self.paths.add(r.path)
        if 400 <= r.status < 500:
            self.n_4xx += 1
        if r.status == 404:
            self.n_404 += 1
        if r.status >= 500:
            self.n_5xx += 1
        if r.status in (401, 403) and AUTH_PATH.match(r.path):
            self.n_auth_fail += 1
        if r.method == "POST":
            self.n_post += 1
        if not r.ua:
            self.n_empty_ua += 1
        elif SCANNER_UA.search(r.ua):
            self.n_scanner_ua += 1
        if novel_ua:
            self.n_novel_ua += 1
        self.bytes_sum += r.bytes_sent
        self.bytes_max = max(self.bytes_max, r.bytes_sent)
        self.req_len_log_sum += math.log1p(r.request_length)
        self.uas.add(r.ua)
        self.timestamps.append(r.ts)

    def interarrival_cv(self) -> float:
        if len(self.timestamps) < 3:
            return 0.0
        gaps = np.diff(np.sort(np.asarray(self.timestamps)))
        mean = gaps.mean()
        return float(gaps.std() / mean) if mean > 0 else 0.0

    def drift(self) -> tuple[float, float]:
        return self.drift_values

    def vector(self) -> np.ndarray:
        n = max(self.count, 1)
        return np.array(
            [
                math.log1p(self.count),
                len(self.paths) / n,
                self.n_4xx / n,
                self.n_auth_fail / n,
                self.n_404 / n,
                self.n_5xx / n,
                math.log1p(self.bytes_sum),
                math.log1p(self.bytes_max),
                self.req_len_log_sum / n,
                self.n_post / n,
                math.log1p(len(self.uas)),
                self.n_scanner_ua / n,
                self.n_novel_ua / n,
                self.interarrival_cv(),
                *self.drift(),
            ],
            dtype=np.float32,
        )

    def summary(self) -> dict:
        return {
            "requests": self.count,
            "unique_paths": len(self.paths),
            "status_4xx": self.n_4xx,
            "status_404": self.n_404,
            "auth_failures": self.n_auth_fail,
            "bytes_out": self.bytes_sum,
            "user_agents": sorted(self.uas)[:5],
        }


class KnownUAs:
    """Bounded per-peer memory of user agents seen in earlier windows."""

    def __init__(self, max_peers: int = 10_000, max_per_peer: int = 32):
        self.max_peers = max_peers
        self.max_per_peer = max_per_peer
        self._data: OrderedDict[str, OrderedDict[str, None]] = OrderedDict()

    def seen(self, peer: str, ua: str) -> bool:
        uas = self._data.get(peer)
        return bool(uas) and ua in uas

    def remember(self, peer: str, uas: set[str]) -> None:
        known = self._data.setdefault(peer, OrderedDict())
        self._data.move_to_end(peer)
        for ua in uas:
            known[ua] = None
            known.move_to_end(ua)
        while len(known) > self.max_per_peer:
            known.popitem(last=False)
        while len(self._data) > self.max_peers:
            self._data.popitem(last=False)


class PeerBaselines:
    """Bounded per-peer fast/slow EWMAs of bytes out and request count per window."""

    def __init__(self, max_peers: int = 10_000):
        self.max_peers = max_peers
        # peer -> (fast_bytes, slow_bytes, fast_count, slow_count)
        self._data: OrderedDict[str, tuple[float, float, float, float]] = OrderedDict()

    def observe(self, peer: str, bytes_out: float, count: float) -> tuple[float, float]:
        """Fold in a closed window and return its drift (log fast/slow ratio)."""
        old = self._data.get(peer)
        if old is None:
            self._data[peer] = (bytes_out, bytes_out, count, count)
            drift = (0.0, 0.0)
        else:
            fb, sb, fc, sc = old
            fb = (1 - FAST_ALPHA) * fb + FAST_ALPHA * bytes_out
            fc = (1 - FAST_ALPHA) * fc + FAST_ALPHA * count
            # the window is compared with the slow baseline *before* it absorbs it
            drift = (math.log1p(fb) - math.log1p(sb), math.log1p(fc) - math.log1p(sc))
            sb = (1 - SLOW_ALPHA) * sb + SLOW_ALPHA * bytes_out
            sc = (1 - SLOW_ALPHA) * sc + SLOW_ALPHA * count
            self._data[peer] = (fb, sb, fc, sc)
        self._data.move_to_end(peer)
        while len(self._data) > self.max_peers:
            self._data.popitem(last=False)
        return drift


class WindowManager:
    """Assigns requests to per-peer tumbling windows in event time and emits closed ones."""

    def __init__(self) -> None:
        self.open: dict[str, Window] = {}
        self.known_uas = KnownUAs()
        self.baselines = PeerBaselines()
        self.watermark = 0.0

    def add(self, r: Request) -> tuple[Window, list[Window]]:
        """Add a request. Returns its window and any windows closed as a result."""
        closed: list[Window] = []
        start = math.floor(r.ts / WINDOW_SECONDS) * WINDOW_SECONDS
        w = self.open.get(r.peer_ip)
        if w is not None and w.start != start:
            if start > w.start:
                closed.append(self._close(r.peer_ip))
                w = None
            else:
                start = w.start  # late event: fold into the current window
        if w is None:
            w = self.open[r.peer_ip] = Window(r.peer_ip, start)
        w.add(r, novel_ua=not self.known_uas.seen(r.peer_ip, r.ua))
        closed.extend(self.advance(r.ts))
        return w, closed

    def advance(self, now: float) -> list[Window]:
        """Close windows that ended before `now - grace` (call periodically when idle)."""
        self.watermark = max(self.watermark, now)
        due = [p for p, w in self.open.items() if w.end + GRACE_SECONDS <= self.watermark]
        return [self._close(p) for p in due]

    def flush(self) -> list[Window]:
        return [self._close(p) for p in list(self.open)]

    def _close(self, peer: str) -> Window:
        w = self.open.pop(peer)
        self.known_uas.remember(peer, w.uas)
        w.drift_values = self.baselines.observe(peer, float(w.bytes_sum), float(w.count))
        return w
