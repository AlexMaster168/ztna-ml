"""Streaming detector: traffic shards -> windows -> rules + models -> events:anomaly.

Scaling. Traffic is split into shard streams by peer (ztna_ml.shards). Workers lease
shards in Redis (ml:lease:{n}) and rebalance as replicas come and go. A shard's state
(open windows, per-peer baselines, model history) is checkpointed to ml:state:{n} in the
same transaction that acknowledges the stream entries it covers, so a shard can move to
another worker, or survive a crash, without losing what was learned about its peers.
Entries processed after the last checkpoint are replayed: delivery is at-least-once and
the trust engine drops duplicate detections.

Latency. Each peer has PHASES window grids offset by WINDOW_SECONDS / PHASES: 30 s
windows that hop every 10 s. Every grid is an ordinary tumbling series, so models see
exactly the windows they were trained on, but one closes every 10 s instead of 30 s.
Model penalties are scaled by 1 / PHASES to keep the trust impact per minute unchanged.

Models. An active model (its detections count) and optionally a shadow challenger
scored on the same windows; both are counted in ml:stats:{version} for comparison, and
the active model's feature distribution is accumulated in ml:drift:{version}:{hour}.

Hot per-peer data for the dashboard:
  metrics:{gw}:{ip}          hash, features of the last closed window
  metrics:{gw}:{ip}:history  list of recent window summaries (newest first)
  recent:{gw}:{ip}           list of recent requests (newest first)
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import signal
import socket
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import redis

from .events import Request, parse_line
from .features import FEATURES, WINDOW_SECONDS, Window, WindowManager
from .model import Scorer, load_scorer
from .rules import Detection, RuleConfig, evaluate
from .shards import SHARDS, stream_name
from .volume import VolumeTracker

log = logging.getLogger("ml-worker")

EVENTS_STREAM = "events:anomaly"
GROUP = "ml-workers"
HISTORY_LEN = 120  # 1 hour of 30s windows
RECENT_LEN = 50
METRICS_TTL = 3600
MIN_REQUESTS_FOR_MODEL = 3
HISTORY_WINDOWS = 32
CHECKPOINT_HISTORY = 12  # enough for the sequence models (seq_len 10)
MAX_TRACKED_PEERS = 10_000
MODEL_KEY = "ml:model"
MODEL_POLL_SECONDS = 10
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

PHASES = int(os.environ.get("ML_PHASES", "3"))
WORKERS_KEY = "ml:workers"
WORKER_TTL = 20
LEASE_MS = 15_000
REBALANCE_SECONDS = 2
CHECKPOINT_SECONDS = 5
ORPHAN_SECONDS = 30
ORPHAN_IDLE_MS = 60_000
STATE_IDLE_TTL = 24 * 3600
STATS_TTL = 7 * 86400
DRIFT_TTL = 2 * 86400
META_FIELD = "__meta__"

HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))

LUA_RENEW = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)
LUA_RELEASE = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


def heartbeat() -> None:
    """Touched every loop; the container healthcheck fails when it goes stale."""
    try:
        HEARTBEAT.touch()
    except OSError:
        pass


def lease_key(shard: int) -> str:
    return f"ml:lease:{shard}"


def state_key(shard: int) -> str:
    return f"ml:state:{shard}"


@dataclass
class Models:
    """Shared by every shard's detector; swapped in place when the deployment changes."""

    active: Scorer | None = None
    shadow: Scorer | None = None


@dataclass
class Scored:
    ratio: float | None = None  # active model, >= 1 is anomalous
    detection: Detection | None = None
    shadow_ratio: float | None = None


class Detector:
    """Pure detection logic for a set of peers; Redis-free so it can be tested and reused."""

    def __init__(
        self,
        scorer: Scorer | None = None,
        config: RuleConfig | None = None,
        phases: int = PHASES,
        models: Models | None = None,
    ):
        self.models = models or Models(scorer)
        self.config = config or RuleConfig()
        self.phases = phases
        self.penalty_scale = float(os.environ.get("MODEL_PENALTY_SCALE", 1 / phases))
        self.managers: dict[str, list[WindowManager]] = {}
        self.volume: dict[str, VolumeTracker] = {}
        # Last closed windows per peer and phase, for sequence models.
        self.history: OrderedDict[tuple[str, str, int], deque[np.ndarray]] = OrderedDict()
        # Start times of the windows in `history` (same keys, same order).
        self.starts: dict[tuple[str, str, int], deque[float]] = {}
        # Peers whose state changed since the last checkpoint.
        self.dirty: set[tuple[str, str]] = set()

    @property
    def scorer(self) -> Scorer | None:
        return self.models.active

    def grid(self, gw: str) -> list[WindowManager]:
        g = self.managers.get(gw)
        if g is None:
            hop = WINDOW_SECONDS / self.phases
            g = self.managers[gw] = [WindowManager(offset=k * hop) for k in range(self.phases)]
        return g

    def on_request(
        self, gw: str, r: Request
    ) -> tuple[Window, list[Detection], list[tuple[int, Window]]]:
        """Returns the peer's phase-0 window, rule detections on it, and closed windows."""
        closed: list[tuple[int, Window]] = []
        window = None
        for k, m in enumerate(self.grid(gw)):
            w, done = m.add(r)
            if k == 0:
                window = w
            closed += [(k, c) for c in done]
        self.dirty.add((gw, r.peer_ip))
        assert window is not None
        return window, evaluate(window, self.config), closed

    def advance(self, now: float) -> list[tuple[str, int, Window]]:
        out = []
        for gw, grid in self.managers.items():
            for k, m in enumerate(grid):
                for w in m.advance(now):
                    self.dirty.add((gw, w.peer_ip))
                    out.append((gw, k, w))
        return out

    def peer_rules(self, gw: str, w: Window) -> list[Detection]:
        """Rules over a peer's recent windows; call once per closed phase-0 window."""
        d = self.volume.setdefault(gw, VolumeTracker()).observe(w.peer_ip, w.bytes_sum)
        return [d] if d else []

    def _remember(self, gw: str, phase: int, w: Window) -> deque[np.ndarray]:
        key = (gw, w.peer_ip, phase)
        h = self.history.get(key)
        if h is None:
            h = self.history[key] = deque(maxlen=HISTORY_WINDOWS)
            self.starts[key] = deque(maxlen=HISTORY_WINDOWS)
            while len(self.history) > MAX_TRACKED_PEERS * self.phases:
                old, _ = self.history.popitem(last=False)
                self.starts.pop(old, None)
        self.history.move_to_end(key)
        h.append(w.vector())
        self.starts.setdefault(key, deque(maxlen=HISTORY_WINDOWS)).append(w.start)
        return h

    def score_window(self, w: Window, gw: str = "gw-1", phase: int = 0) -> Scored:
        """Call once per closed window."""
        history = self._remember(gw, phase, w)
        out = Scored()
        if w.count < MIN_REQUESTS_FOR_MODEL:
            return out
        shadow = self.models.shadow
        if shadow is not None and len(history) >= shadow.history_needed:
            out.shadow_ratio = shadow.score(history)
        active = self.models.active
        if active is None or len(history) < active.history_needed:
            return out
        out.ratio = ratio = active.score(history)
        if ratio >= 1.0:
            # Borderline exceedances cost little; clear anomalies up to the cap.
            penalty = round(min(50.0, 10.0 + 12.0 * (ratio - 1.0)) * self.penalty_scale, 1)
            out.detection = Detection(
                "model",
                "traffic_anomaly",
                penalty,
                {
                    "ratio": round(ratio, 2),
                    "model": active.version,
                    "top_features": active.explain(history),
                    "phase": phase,
                },
            )
            # Oldest traffic the judgement rests on: a sequence model still "sees"
            # minutes of history, which a human release must be able to forgive.
            since = self._since(gw, w.peer_ip, phase, active.history_needed)
            if since is not None:
                out.detection.details["since"] = int(since)
        return out

    def _since(self, gw: str, ip: str, phase: int, needed: int) -> float | None:
        """Start of the oldest window in the scored sequence, if known (state restored
        from older checkpoints carries no start times)."""
        starts = list(self.starts.get((gw, ip, phase)) or ())
        if not starts:
            return None
        oldest = starts[-min(max(needed, 1), len(starts))]
        return oldest or None

    # Checkpointing: per-peer state as JSON-able dicts.
    def export_peer(self, gw: str, ip: str) -> dict:
        vol = self.volume.get(gw)
        return {
            "phases": [m.export_peer(ip) for m in self.grid(gw)],
            "volume": vol.export_peer(ip) if vol else None,
            "history": [
                [
                    [round(float(x), 5) for x in v]
                    for v in list(self.history.get((gw, ip, k), ()))[-CHECKPOINT_HISTORY:]
                ]
                for k in range(self.phases)
            ],
            "starts": [
                list(self.starts.get((gw, ip, k), ()))[-CHECKPOINT_HISTORY:]
                for k in range(self.phases)
            ],
        }

    def import_peer(self, gw: str, ip: str, d: dict) -> None:
        for m, pd in zip(self.grid(gw), d.get("phases") or [], strict=False):
            m.import_peer(ip, pd)
        self.volume.setdefault(gw, VolumeTracker()).import_peer(ip, d.get("volume"))
        for k, h in enumerate((d.get("history") or [])[: self.phases]):
            if h:
                self.history[(gw, ip, k)] = deque(
                    (np.asarray(v, dtype=np.float32) for v in h), maxlen=HISTORY_WINDOWS
                )
                starts = (d.get("starts") or [[]] * self.phases)[k] or [0.0] * len(h)
                self.starts[(gw, ip, k)] = deque(starts, maxlen=HISTORY_WINDOWS)

    def forget(self, gw: str, ip: str) -> None:
        for m in self.managers.get(gw, []):
            m.open.pop(ip, None)
        for k in range(self.phases):
            self.history.pop((gw, ip, k), None)
            self.starts.pop((gw, ip, k), None)

    def watermarks(self) -> dict[str, float]:
        return {gw: max(m.watermark for m in grid) for gw, grid in self.managers.items()}

    def restore_watermarks(self, marks: dict[str, float]) -> None:
        for gw, mark in marks.items():
            for m in self.grid(gw):
                m.watermark = max(m.watermark, mark)


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


class LostLease(Exception):
    pass


@dataclass
class Shard:
    n: int
    detector: Detector
    unacked: list[bytes] = field(default_factory=list)
    last_seen: dict[str, float] = field(default_factory=dict)  # "gw|ip" -> unix time


class Worker:
    def __init__(self, r: redis.Redis, consumer: str, models_dir: Path, shards: int = SHARDS):
        self.r = r
        self.consumer = consumer
        self.models_dir = models_dir
        self.n_shards = shards
        self.models = Models()
        self.versions: dict[str, str | None] = {"active": None, "shadow": None}
        self.owned: dict[int, Shard] = {}
        # (version, hour) -> [n, sum, sum of squares] of the active model's normalised features
        self.drift: dict[tuple[str, int], list] = {}
        self._renew = r.register_script(LUA_RENEW)
        self._release = r.register_script(LUA_RELEASE)
        self._due: dict[str, float] = {}

    # --- model deployment -------------------------------------------------------------
    def refresh_model(self) -> None:
        """Follow the deployment chosen in the orchestrator (Redis hash ml:model)."""
        cfg = {k.decode(): v.decode() for k, v in self.r.hgetall(MODEL_KEY).items()}
        if "active" in cfg or "shadow" in cfg:
            want = {"active": cfg.get("active") or None, "shadow": cfg.get("shadow") or None}
        else:  # {version, mode} written before champion/challenger
            v = cfg.get("version") or None
            active = cfg.get("mode") == "active"
            want = {"active": v if active else None, "shadow": None if active else v}
        for slot in ("active", "shadow"):
            version = want[slot]
            if version == self.versions[slot]:
                continue
            if version is None:
                setattr(self.models, slot, None)
                self.versions[slot] = None
                log.info("%s model undeployed", slot)
                continue
            if not VERSION_RE.match(version):
                log.error("refusing suspicious model version %r", version)
                continue
            other = "shadow" if slot == "active" else "active"
            scorer = getattr(self.models, other) if self.versions[other] == version else None
            if scorer is None:
                try:
                    scorer = load_scorer(self.models_dir / version)
                except Exception:
                    log.exception("failed to load model %s", version)
                    continue
                if scorer is None:
                    continue
            setattr(self.models, slot, scorer)
            self.versions[slot] = version
            log.info("%s model: %s", slot, version)
        key = f"ml:worker:{self.consumer}"
        self.r.hset(
            key,
            mapping={
                "active": self.versions["active"] or "",
                "shadow": self.versions["shadow"] or "",
                "shards": ",".join(map(str, sorted(self.owned))),
                "seen_at": int(time.time()),
            },
        )
        self.r.expire(key, 120)

    # --- shard ownership --------------------------------------------------------------
    def rebalance(self) -> None:
        now = time.time()
        self.r.zadd(WORKERS_KEY, {self.consumer: now})
        self.r.zremrangebyscore(WORKERS_KEY, "-inf", now - WORKER_TTL)
        alive = max(int(self.r.zcard(WORKERS_KEY)), 1)
        target = math.ceil(self.n_shards / alive)
        for n in list(self.owned):
            if not self._renew(keys=[lease_key(n)], args=[self.consumer, LEASE_MS]):
                log.warning("lost lease on shard %s", n)
                del self.owned[n]
        while len(self.owned) > target:
            self.release(max(self.owned))
        if len(self.owned) < target:
            free = [n for n in range(self.n_shards) if n not in self.owned]
            random.shuffle(free)
            for n in free:
                if len(self.owned) >= target:
                    break
                if self.r.set(lease_key(n), self.consumer, nx=True, px=LEASE_MS):
                    self.acquire(n)

    def acquire(self, n: int) -> None:
        stream = stream_name(n)
        try:
            self.r.xgroup_create(stream, GROUP, id="$", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        shard = Shard(n, Detector(models=self.models))
        now = time.time()
        restored = 0
        for raw_field, raw in self.r.hgetall(state_key(n)).items():
            f = raw_field.decode()
            d = json.loads(raw)
            if f == META_FIELD:
                shard.detector.restore_watermarks(d.get("watermarks", {}))
                continue
            gw, ip = f.split("|", 1)
            shard.detector.import_peer(gw, ip, d)
            shard.last_seen[f] = float(d.get("seen", now))
            restored += 1
        self.owned[n] = shard
        log.info("acquired shard %s (%d peers restored)", n, restored)
        # Whatever the previous owner read after its last checkpoint is still pending.
        self.claim(n, min_idle_ms=0)

    def claim(self, n: int, min_idle_ms: int) -> None:
        cursor: bytes | str = "0-0"
        while n in self.owned:
            cursor, entries, *_ = self.r.xautoclaim(
                stream_name(n), GROUP, self.consumer, min_idle_ms, start_id=cursor, count=500
            )
            if entries:
                self.process(n, entries)
            if not entries or cursor in (b"0-0", "0-0") or cursor == entries[-1][0]:
                break

    def release(self, n: int) -> None:
        try:
            self.checkpoint(n)
        finally:
            self._release(keys=[lease_key(n)], args=[self.consumer])
            self.owned.pop(n, None)
            log.info("released shard %s", n)

    def checkpoint(self, n: int) -> None:
        """Persist dirty peer state and ack the entries it covers, atomically and only
        while this worker still holds the shard's lease."""
        sh = self.owned.get(n)
        if sh is None:
            return
        det = sh.detector
        now = time.time()
        try:
            with self.r.pipeline() as pipe:
                pipe.watch(lease_key(n))
                if pipe.get(lease_key(n)) != self.consumer.encode():
                    raise LostLease
                pipe.multi()
                for gw, ip in det.dirty:
                    f = f"{gw}|{ip}"
                    d = det.export_peer(gw, ip)
                    d["seen"] = sh.last_seen.get(f, now)
                    pipe.hset(state_key(n), f, json.dumps(d, separators=(",", ":")))
                stale = [f for f, t in sh.last_seen.items() if now - t > STATE_IDLE_TTL]
                if stale:
                    pipe.hdel(state_key(n), *stale)
                pipe.hset(state_key(n), META_FIELD, json.dumps({"watermarks": det.watermarks()}))
                if sh.unacked:
                    pipe.xack(stream_name(n), GROUP, *sh.unacked)
                pipe.execute()
        except (LostLease, redis.WatchError):
            log.warning("shard %s moved to another worker; dropping local state", n)
            self.owned.pop(n, None)
            return
        for f in stale:
            sh.last_seen.pop(f, None)
            det.forget(*f.split("|", 1))
        det.dirty.clear()
        sh.unacked.clear()

    def shutdown(self) -> None:
        for n in list(self.owned):
            try:
                self.release(n)
            except redis.RedisError:
                pass
        try:
            self.r.zrem(WORKERS_KEY, self.consumer)
        except redis.RedisError:
            pass

    # --- processing -------------------------------------------------------------------
    def process(self, n: int, entries: list[tuple[bytes, dict[bytes, bytes]]]) -> None:
        sh = self.owned.get(n)
        if sh is None:
            return
        pipe = self.r.pipeline(transaction=False)
        now = time.time()
        for entry_id, fields in entries:
            sh.unacked.append(entry_id)
            if not fields:
                continue
            gw = fields.get(b"gw", b"gw-1").decode()
            req = parse_line(fields.get(b"data", b""))
            if req is None:
                continue
            sh.last_seen[f"{gw}|{req.peer_ip}"] = now
            window, detections, closed = sh.detector.on_request(gw, req)
            for d in detections:
                self._emit(pipe, gw, req.peer_ip, window.start, d)
            for phase, w in closed:
                self._on_closed(pipe, sh, gw, phase, w)
            key = f"recent:{gw}:{req.peer_ip}"
            pipe.lpush(key, json.dumps(asdict(req)))
            pipe.ltrim(key, 0, RECENT_LEN - 1)
            pipe.expire(key, METRICS_TTL)
        pipe.execute()

    def tick(self) -> None:
        now = time.time()
        for sh in list(self.owned.values()):
            closed = sh.detector.advance(now)
            if not closed:
                continue
            pipe = self.r.pipeline(transaction=False)
            for gw, phase, w in closed:
                self._on_closed(pipe, sh, gw, phase, w)
            pipe.execute()

    def _emit(self, pipe, gw: str, ip: str, window_start: float, d: Detection) -> None:
        log.info(
            "detection %s/%s peer=%s penalty=%s %s", d.source, d.kind, ip, d.penalty, d.details
        )
        pipe.xadd(EVENTS_STREAM, event_record(gw, ip, window_start, d), maxlen=100_000)

    def _count(self, pipe, version: str | None, ratio: float | None, other: float | None):
        if version is None or ratio is None:
            return
        key = f"ml:stats:{version}"
        pipe.hincrby(key, "windows", 1)
        if ratio >= 1:
            pipe.hincrby(key, "anomalies", 1)
        if other is not None and (ratio >= 1) != (other >= 1):
            pipe.hincrby(key, "disagreements", 1)
        pipe.expire(key, STATS_TTL)

    def _on_closed(self, pipe, sh: Shard, gw: str, phase: int, w: Window) -> None:
        det = sh.detector
        if phase == 0:
            for d in det.peer_rules(gw, w):
                self._emit(pipe, gw, w.peer_ip, w.start, d)
        scored = det.score_window(w, gw, phase)
        if scored.detection is not None:
            self._emit(pipe, gw, w.peer_ip, w.start, scored.detection)
        self._count(pipe, self.versions["active"], scored.ratio, scored.shadow_ratio)
        self._count(pipe, self.versions["shadow"], scored.shadow_ratio, scored.ratio)
        if phase != 0:
            return
        if scored.ratio is not None:
            self._accumulate_drift(w)
        key = f"metrics:{gw}:{w.peer_ip}"
        vec = w.vector()
        snapshot = {
            "window_start": int(w.start),
            **w.summary(),
            "anomaly_ratio": None if scored.ratio is None else round(scored.ratio, 3),
        }
        mapping = {f: float(v) for f, v in zip(FEATURES, vec, strict=True)}
        mapping["window_start"] = int(w.start)
        mapping["anomaly_ratio"] = "" if scored.ratio is None else scored.ratio
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, METRICS_TTL)
        pipe.lpush(f"{key}:history", json.dumps(snapshot))
        pipe.ltrim(f"{key}:history", 0, HISTORY_LEN - 1)
        pipe.expire(f"{key}:history", METRICS_TTL)

    def _accumulate_drift(self, w: Window) -> None:
        scorer, version = self.models.active, self.versions["active"]
        normalizer = getattr(scorer, "normalizer", None)
        if normalizer is None or version is None:
            return
        z = normalizer.transform(w.vector()[None, :])[0].astype(np.float64)
        acc = self.drift.setdefault(
            (version, int(time.time() // 3600)), [0, np.zeros_like(z), np.zeros_like(z)]
        )
        acc[0] += 1
        acc[1] += z
        acc[2] += z * z

    def flush_drift(self) -> None:
        if not self.drift:
            return
        pipe = self.r.pipeline(transaction=False)
        for (version, hour), (n, s, q) in self.drift.items():
            key = f"ml:drift:{version}:{hour}"
            pipe.hincrbyfloat(key, "n", n)
            for i, name in enumerate(FEATURES):
                pipe.hincrbyfloat(key, f"s:{name}", float(s[i]))
                pipe.hincrbyfloat(key, f"q:{name}", float(q[i]))
            pipe.expire(key, DRIFT_TTL)
        pipe.execute()
        self.drift.clear()

    # --- main loop --------------------------------------------------------------------
    def _is_due(self, name: str, every: float) -> bool:
        now = time.monotonic()
        if now - self._due.get(name, 0.0) >= every:
            self._due[name] = now
            return True
        return False

    def step(self, block_ms: int = 500) -> None:
        if self._is_due("rebalance", REBALANCE_SECONDS):
            heartbeat()
            self.rebalance()
        if self._is_due("model", MODEL_POLL_SECONDS):
            self.refresh_model()
        streams = {stream_name(n): ">" for n in self.owned}
        if streams:
            resp = self.r.xreadgroup(GROUP, self.consumer, streams, count=500, block=block_ms)
            for stream, entries in resp or []:
                name = stream.decode() if isinstance(stream, bytes) else stream
                self.process(int(name.rsplit(":", 1)[1]), entries)
        else:
            time.sleep(block_ms / 1000)
        if self._is_due("tick", 1):
            self.tick()
        if self._is_due("orphans", ORPHAN_SECONDS):
            for n in list(self.owned):
                self.claim(n, ORPHAN_IDLE_MS)
        if self._is_due("checkpoint", CHECKPOINT_SECONDS):
            for n in list(self.owned):
                self.checkpoint(n)
            self.flush_drift()

    def run(self) -> None:
        while True:
            self.step()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[ml-worker] %(levelname)s %(message)s")
    r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    consumer = os.environ.get("CONSUMER_NAME", socket.gethostname())
    models_dir = Path(os.environ.get("MODELS_DIR", "/models"))
    worker = Worker(r, consumer, models_dir)

    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    try:
        while True:
            try:
                worker.run()
            except redis.ConnectionError as exc:
                # Leases may expire meanwhile; renewal then fails and the shards are
                # re-acquired from their checkpoints.
                log.warning("redis connection lost (%s); reconnecting", exc)
                time.sleep(2)
    finally:
        log.info("shutting down, handing shards back")
        worker.shutdown()


if __name__ == "__main__":
    main()
