"""Streaming detector: traffic:http -> windows -> rules + model -> events:anomaly.

Also maintains hot per-peer metrics in Redis for the dashboard:
  metrics:{gw}:{ip}          hash, features of the last closed window
  metrics:{gw}:{ip}:history  list of recent window summaries (newest first)
  recent:{gw}:{ip}           list of recent requests (newest first)
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
from collections import OrderedDict, deque
from dataclasses import asdict
from pathlib import Path

import numpy as np
import redis

from .events import Request, parse_line
from .features import FEATURES, Window, WindowManager
from .model import Scorer, load_scorer
from .rules import Detection, RuleConfig, evaluate
from .volume import VolumeTracker

log = logging.getLogger("ml-worker")

TRAFFIC_STREAM = "traffic:http"
EVENTS_STREAM = "events:anomaly"
GROUP = "ml-workers"
HISTORY_LEN = 120  # 1 hour of 30s windows
RECENT_LEN = 50
METRICS_TTL = 3600
MIN_REQUESTS_FOR_MODEL = 3
HISTORY_WINDOWS = 32
MAX_TRACKED_PEERS = 10_000
MODEL_KEY = "ml:model"
MODEL_POLL_SECONDS = 10
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))


def heartbeat() -> None:
    """Touched every loop; the container healthcheck fails when it goes stale."""
    try:
        HEARTBEAT.touch()
    except OSError:
        pass


class Detector:
    """Pure detection logic; Redis-free so it can be tested and reused offline."""

    def __init__(self, scorer: Scorer | None, config: RuleConfig | None = None):
        self.scorer = scorer
        self.config = config or RuleConfig()
        self.managers: dict[str, WindowManager] = {}
        self.volume: dict[str, VolumeTracker] = {}
        # Last closed windows per peer, for sequence models.
        self.history: OrderedDict[tuple[str, str], deque[np.ndarray]] = OrderedDict()

    def _remember(self, gw: str, w: Window) -> deque[np.ndarray]:
        key = (gw, w.peer_ip)
        h = self.history.get(key)
        if h is None:
            h = self.history[key] = deque(maxlen=HISTORY_WINDOWS)
            while len(self.history) > MAX_TRACKED_PEERS:
                self.history.popitem(last=False)
        self.history.move_to_end(key)
        h.append(w.vector())
        return h

    def _manager(self, gw: str) -> WindowManager:
        return self.managers.setdefault(gw, WindowManager())

    def on_request(self, gw: str, r: Request) -> tuple[Window, list[Detection], list[Window]]:
        window, closed = self._manager(gw).add(r)
        return window, evaluate(window, self.config), closed

    def advance(self, now: float) -> list[tuple[str, Window]]:
        return [(gw, w) for gw, m in self.managers.items() for w in m.advance(now)]

    def peer_rules(self, gw: str, w: Window) -> list[Detection]:
        """Rules over a peer's recent windows; call once per closed window."""
        d = self.volume.setdefault(gw, VolumeTracker()).observe(w.peer_ip, w.bytes_sum)
        return [d] if d else []

    def score_window(self, w: Window, gw: str = "gw-1") -> tuple[float | None, Detection | None]:
        """Call once per closed window. Returns (anomaly ratio, detection);
        ratio >= 1 means above the model threshold."""
        history = self._remember(gw, w)
        if self.scorer is None or w.count < MIN_REQUESTS_FOR_MODEL:
            return None, None
        if len(history) < self.scorer.history_needed:
            return None, None
        ratio = self.scorer.score(history)
        if ratio < 1.0:
            return ratio, None
        # Borderline exceedances cost little; clear anomalies up to the cap.
        penalty = round(min(50.0, 10.0 + 12.0 * (ratio - 1.0)), 1)
        top = self.scorer.explain(history)
        return ratio, Detection(
            "model",
            "traffic_anomaly",
            penalty,
            {"ratio": round(ratio, 2), "model": self.scorer.version, "top_features": top},
        )


def event_record(gw: str, peer_ip: str, window_start: float, d: Detection) -> dict[str, str]:
    return {
        "gw": gw,
        "peer_ip": peer_ip,
        "source": d.source,
        "kind": d.kind,
        "penalty": str(d.penalty),
        "window_start": str(int(window_start)),
        "ts": str(time.time()),
        "details": json.dumps(d.details),
    }


class Worker:
    def __init__(self, r: redis.Redis, detector: Detector, consumer: str, models_dir: Path):
        self.r = r
        self.detector = detector
        self.consumer = consumer
        self.models_dir = models_dir
        self.model_version: str | None = None
        self.shadow = True
        self._last_model_check = 0.0

    def refresh_model(self) -> None:
        """Follow the deployment chosen in the orchestrator (Redis hash ml:model)."""
        cfg = {k.decode(): v.decode() for k, v in self.r.hgetall(MODEL_KEY).items()}
        version = cfg.get("version") or None
        if version != self.model_version:
            if version is None:
                self.detector.scorer = None
                log.info("model undeployed; rules only")
            elif not VERSION_RE.match(version):
                log.error("refusing suspicious model version %r", version)
                return
            else:
                try:
                    scorer = load_scorer(self.models_dir / version)
                except Exception:
                    log.exception(
                        "failed to load model %s; keeping %s", version, self.model_version
                    )
                    return
                if scorer is None:
                    return
                self.detector.scorer = scorer
                log.info("loaded model %s", version)
            self.model_version = version
        shadow = cfg.get("mode") != "active"
        if shadow != self.shadow:
            log.info("model mode: %s", "shadow" if shadow else "active")
            self.shadow = shadow
        self.r.hset(
            f"ml:worker:{self.consumer}",
            mapping={
                "version": self.model_version or "",
                "mode": "shadow" if self.shadow else "active",
                "seen_at": int(time.time()),
            },
        )
        self.r.expire(f"ml:worker:{self.consumer}", 120)

    def setup(self) -> None:
        try:
            self.r.xgroup_create(TRAFFIC_STREAM, GROUP, id="$", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def process(self, entries: list[tuple[bytes, dict[bytes, bytes]]]) -> None:
        pipe = self.r.pipeline(transaction=False)
        for _id, fields in entries:
            gw = fields.get(b"gw", b"gw-1").decode()
            req = parse_line(fields.get(b"data", b""))
            if req is None:
                continue
            window, detections, closed = self.detector.on_request(gw, req)
            for d in detections:
                self._emit(pipe, gw, req.peer_ip, window.start, d)
            for w in closed:
                self._on_closed(pipe, gw, w)
            key = f"recent:{gw}:{req.peer_ip}"
            pipe.lpush(key, json.dumps(asdict(req)))
            pipe.ltrim(key, 0, RECENT_LEN - 1)
            pipe.expire(key, METRICS_TTL)
        if entries:
            pipe.xack(TRAFFIC_STREAM, GROUP, *[e[0] for e in entries])
        pipe.execute()

    def tick(self) -> None:
        closed = self.detector.advance(time.time())
        if not closed:
            return
        pipe = self.r.pipeline(transaction=False)
        for gw, w in closed:
            self._on_closed(pipe, gw, w)
        pipe.execute()

    def _emit(self, pipe, gw: str, ip: str, window_start: float, d: Detection) -> None:
        log.info(
            "detection %s/%s peer=%s penalty=%s %s", d.source, d.kind, ip, d.penalty, d.details
        )
        pipe.xadd(EVENTS_STREAM, event_record(gw, ip, window_start, d), maxlen=100_000)

    def _on_closed(self, pipe, gw: str, w: Window) -> None:
        for d in self.detector.peer_rules(gw, w):
            self._emit(pipe, gw, w.peer_ip, w.start, d)
        ratio, detection = self.detector.score_window(w, gw)
        if detection is not None:
            if self.shadow:
                log.info("shadow model detection peer=%s %s", w.peer_ip, detection.details)
            else:
                self._emit(pipe, gw, w.peer_ip, w.start, detection)
        key = f"metrics:{gw}:{w.peer_ip}"
        vec = w.vector()
        snapshot = {
            "window_start": int(w.start),
            **w.summary(),
            "anomaly_ratio": None if ratio is None else round(ratio, 3),
        }
        mapping = {f: float(v) for f, v in zip(FEATURES, vec, strict=True)}
        mapping["window_start"] = int(w.start)
        mapping["anomaly_ratio"] = "" if ratio is None else ratio
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, METRICS_TTL)
        pipe.lpush(f"{key}:history", json.dumps(snapshot))
        pipe.ltrim(f"{key}:history", 0, HISTORY_LEN - 1)
        pipe.expire(f"{key}:history", METRICS_TTL)

    def reclaim_stale(self) -> None:
        """Take over messages a crashed consumer read but never acked."""
        _next, entries, *_ = self.r.xautoclaim(
            TRAFFIC_STREAM, GROUP, self.consumer, min_idle_time=60_000, count=1000
        )
        if entries:
            log.info("reclaimed %d pending entries", len(entries))
            self.process(entries)

    def run(self) -> None:
        self.setup()
        self.reclaim_stale()
        last_tick = time.monotonic()
        while True:
            resp = self.r.xreadgroup(
                GROUP, self.consumer, {TRAFFIC_STREAM: ">"}, count=500, block=1000
            )
            for _stream, entries in resp or []:
                self.process(entries)
            if time.monotonic() - last_tick >= 1:
                heartbeat()
                self.tick()
                last_tick = time.monotonic()
            if time.monotonic() - self._last_model_check >= MODEL_POLL_SECONDS:
                self.refresh_model()
                self._last_model_check = time.monotonic()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[ml-worker] %(levelname)s %(message)s")
    r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    consumer = os.environ.get("CONSUMER_NAME", socket.gethostname())
    models_dir = Path(os.environ.get("MODELS_DIR", "/models"))
    worker = Worker(r, Detector(None), consumer, models_dir)  # state survives reconnects
    while True:
        try:
            worker.run()
        except redis.ConnectionError as exc:
            log.warning("redis connection lost (%s); reconnecting", exc)
            time.sleep(2)


if __name__ == "__main__":
    main()
