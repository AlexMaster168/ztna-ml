"""Continuous training service.

Retrains the model on the network's own recent traffic when a scheduled interval has
passed, when the active model's input distribution drifts, or on request from the
dashboard. A new model must pass quality gates; the result is handed to the orchestrator
(stream ml:train:results), which registers it and deploys it as the shadow challenger.

    python -m ztna_ml.trainer

Policy comes from ml:trainer:config (mirrored from the orchestrator's MLOps settings).
State for the dashboard is kept in ml:trainer:status and ml:drift:latest.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import redis

from .features import FEATURES
from .livecheck import Span

log = logging.getLogger("trainer")

CONFIG_KEY = "ml:trainer:config"
STATUS_KEY = "ml:trainer:status"
REQUESTS_KEY = "ml:train:requests"
RESULTS_STREAM = "ml:train:results"
DRIFT_LATEST = "ml:drift:latest"
MODEL_KEY = "ml:model"
SUSPICIOUS_KEY = "ml:suspicious"
# Traffic left out of the baseline around each incident: the attack began before it was
# detected and may go on until enforcement stops it. The peer's other traffic stays in,
# so a model that misjudged a normal peer can still learn its behaviour.
SUSPECT_BEFORE = float(os.environ.get("SUSPECT_BEFORE_MINUTES", "15")) * 60
SUSPECT_AFTER = float(os.environ.get("SUSPECT_AFTER_MINUTES", "15")) * 60

LOOP_SECONDS = 30
DRIFT_COOLDOWN = 2 * 3600  # minimum time between drift-triggered runs
REFERENCE_WINDOWS = 500  # first windows after deployment define "normal" for drift
MIN_DRIFT_WINDOWS = 100
TRAIN_TIMEOUT = 3 * 3600
HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))

# Quality gates for a candidate (report.json of ztna_ml.train).
GATE_MAX_FPR = float(os.environ.get("GATE_MAX_FPR", "0.01"))  # synthetic hold-out
GATE_MIN_RECALL = float(os.environ.get("GATE_MIN_RECALL", "0.6"))
GATE_MAX_LIVE_FPR = float(os.environ.get("GATE_MAX_LIVE_FPR", "0.12"))  # real hold-out


def heartbeat() -> None:
    try:
        HEARTBEAT.touch()
    except OSError:
        pass


@dataclass
class Config:
    auto_retrain: bool = True
    retrain_interval_hours: float = 24
    drift_threshold: float = 0.5
    model_kind: str = "lstm"
    train_real_minutes: int = 90

    @classmethod
    def load(cls, r: redis.Redis) -> Config:
        h = {k.decode(): v.decode() for k, v in r.hgetall(CONFIG_KEY).items()}
        c = cls()
        if h:
            c.auto_retrain = h.get("auto_retrain", "1") == "1"
            c.retrain_interval_hours = float(h.get("retrain_interval_hours", 24))
            c.drift_threshold = float(h.get("drift_threshold", 0.5))
            c.model_kind = h.get("model_kind", "lstm")
            c.train_real_minutes = int(float(h.get("train_real_minutes", 90)))
        if c.model_kind not in ("lstm", "dense"):
            c.model_kind = "lstm"
        return c


def _sums(h: dict[bytes, bytes]) -> tuple[float, dict[str, float], dict[str, float]]:
    d = {k.decode(): float(v) for k, v in h.items()}
    return (
        d.get("n", 0.0),
        {f: d.get(f"s:{f}", 0.0) for f in FEATURES},
        {f: d.get(f"q:{f}", 0.0) for f in FEATURES},
    )


def drift_report(r: redis.Redis, version: str, now: float) -> dict:
    """How far the active model's live inputs moved from its first hours in production.

    Workers accumulate hourly sums of normalised features (ml:drift:{version}:{hour}).
    The first REFERENCE_WINDOWS windows are frozen as the reference; the score is the
    mean over features of |live mean - reference mean| / reference std, i.e. a shift
    in standard deviations.
    """
    ref_key = f"ml:drift:{version}:ref"
    hours = sorted(
        int(k.decode().rsplit(":", 1)[1])
        for k in r.scan_iter(f"ml:drift:{version}:*")
        if k.decode().rsplit(":", 1)[1].isdigit()
    )
    if not r.exists(ref_key):
        n_total, s_total, q_total = 0.0, dict.fromkeys(FEATURES, 0.0), dict.fromkeys(FEATURES, 0.0)
        for h in hours:
            n, s, q = _sums(r.hgetall(f"ml:drift:{version}:{h}"))
            n_total += n
            for f in FEATURES:
                s_total[f] += s[f]
                q_total[f] += q[f]
        if n_total < REFERENCE_WINDOWS:
            return {"version": version, "status": "collecting_reference", "windows": int(n_total)}
        mapping = {"n": n_total, "until_hour": hours[-1]}
        mapping |= {f"s:{f}": s_total[f] for f in FEATURES}
        mapping |= {f"q:{f}": q_total[f] for f in FEATURES}
        r.hset(ref_key, mapping=mapping)
        r.expire(ref_key, 30 * 86400)
    ref_n, ref_s, ref_q = _sums(r.hgetall(ref_key))
    until = int(float(r.hget(ref_key, "until_hour") or 0))
    current_hour = int(now // 3600)
    live_hours = [h for h in hours if h > until and h >= current_hour - 1]
    n, s, q = 0.0, dict.fromkeys(FEATURES, 0.0), dict.fromkeys(FEATURES, 0.0)
    for h in live_hours:
        hn, hs, hq = _sums(r.hgetall(f"ml:drift:{version}:{h}"))
        n += hn
        for f in FEATURES:
            s[f] += hs[f]
            q[f] += hq[f]
    if n < MIN_DRIFT_WINDOWS:
        return {"version": version, "status": "collecting", "windows": int(n)}
    shifts = {}
    for f in FEATURES:
        ref_mean = ref_s[f] / ref_n
        ref_std = math.sqrt(max(ref_q[f] / ref_n - ref_mean**2, 0.0)) or 1.0
        shifts[f] = round(abs(s[f] / n - ref_mean) / max(ref_std, 0.1), 3)
    score = round(sum(shifts.values()) / len(shifts), 3)
    top = sorted(shifts, key=shifts.get, reverse=True)[:5]
    return {
        "version": version,
        "status": "ok",
        "windows": int(n),
        "score": score,
        "top_features": {f: shifts[f] for f in top},
    }


def check_gates(report: dict) -> tuple[bool, str]:
    m = report.get("model", {})
    fpr, recall = m.get("fpr"), m.get("recall")
    if fpr is None or recall is None:
        return False, "report has no metrics"
    if fpr > GATE_MAX_FPR:
        return False, f"fpr {fpr} > {GATE_MAX_FPR}"
    if recall < GATE_MIN_RECALL:
        return False, f"recall {recall} < {GATE_MIN_RECALL}"
    live = report.get("live_holdout") or {}
    if live.get("normal_fpr") is not None and live["normal_fpr"] > GATE_MAX_LIVE_FPR:
        return False, f"live fpr {live['normal_fpr']} > {GATE_MAX_LIVE_FPR}"
    return True, f"fpr {fpr}, recall {recall}, live fpr {live.get('normal_fpr')}"


class Trainer:
    def __init__(self, r: redis.Redis, models_dir: Path, redis_url: str):
        self.r = r
        self.models_dir = models_dir
        self.redis_url = redis_url
        self.started = time.time()

    def status(self) -> dict[str, str]:
        return {k.decode(): v.decode() for k, v in self.r.hgetall(STATUS_KEY).items()}

    def set_status(self, **fields) -> None:
        self.r.hset(STATUS_KEY, mapping={k: str(v) for k, v in fields.items()})

    def active_version(self) -> str | None:
        h = {k.decode(): v.decode() for k, v in self.r.hgetall(MODEL_KEY).items()}
        return h.get("active") or (h.get("version") if h.get("mode") == "active" else None)

    def update_drift(self, now: float) -> dict | None:
        version = self.active_version()
        if not version:
            self.r.delete(DRIFT_LATEST)
            return None
        report = drift_report(self.r, version, now)
        self.r.hset(DRIFT_LATEST, mapping={"report": json.dumps(report), "at": int(now)})
        return report

    def trigger(self, cfg: Config, drift: dict | None, now: float) -> str | None:
        if self.r.lpop(REQUESTS_KEY):
            return "manual"
        if not cfg.auto_retrain:
            return None
        last = float(self.status().get("last_finished_at") or self.started)
        if now - last >= cfg.retrain_interval_hours * 3600:
            return "schedule"
        score = (drift or {}).get("score")
        if score is not None and score >= cfg.drift_threshold and now - last >= DRIFT_COOLDOWN:
            return "drift"
        return None

    def excluded_spans(self, cfg: Config, now: float) -> list[Span]:
        """Suspect traffic around each incident mark ("ip|ts" members; a bare "ip" from
        older versions is taken at its score)."""
        since = now - cfg.train_real_minutes * 60 - SUSPECT_AFTER
        self.r.zremrangebyscore(SUSPICIOUS_KEY, "-inf", since - 24 * 3600)
        spans = []
        for member, ts in self.r.zrangebyscore(SUSPICIOUS_KEY, since, "+inf", withscores=True):
            ip = member.decode().split("|", 1)[0]
            spans.append(Span(ip, ts - SUSPECT_BEFORE, ts + SUSPECT_AFTER))
        return spans

    def command(self, cfg: Config, now: float) -> list[str]:
        cmd = [
            sys.executable, "-m", "ztna_ml.train",
            "--out", str(self.models_dir),
            "--model", cfg.model_kind,
            "--redis-url", self.redis_url,
            "--real-minutes", str(cfg.train_real_minutes),
        ]  # fmt: skip
        for span in self.excluded_spans(cfg, now):
            cmd += ["--exclude-span", str(span)]
        return cmd

    @staticmethod
    def _run(cmd: list[str], started: float) -> tuple[bool, str]:
        """Run the training job, keeping the heartbeat fresh while it works."""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        while True:
            try:
                out, _ = proc.communicate(timeout=10)
                return proc.returncode == 0, out[-2000:]
            except subprocess.TimeoutExpired:
                heartbeat()
                if time.time() - started > TRAIN_TIMEOUT:
                    proc.kill()
                    proc.communicate()
                    return False, "training timed out"

    def run_training(self, cfg: Config, trigger: str) -> dict:
        now = time.time()
        before = {p.name for p in self.models_dir.iterdir()} if self.models_dir.exists() else set()
        self.set_status(state="training", trigger=trigger, started_at=int(now))
        log.info("training (%s, %s)", trigger, cfg.model_kind)
        ok, output = self._run(self.command(cfg, now), now)
        new = sorted(
            (p for p in self.models_dir.iterdir() if p.name not in before and p.is_dir()),
            key=lambda p: p.stat().st_mtime,
        )
        result = {"trigger": trigger, "version": "", "passed": "0"}
        if not ok or not new:
            tail = output.strip().splitlines()
            result["reason"] = "training failed: " + (tail[-1][:300] if tail else "no output")
        else:
            version = new[-1].name
            report = json.loads((new[-1] / "report.json").read_text())
            passed, reason = check_gates(report)
            result |= {"version": version, "passed": "1" if passed else "0", "reason": reason}
            self.r.xadd(RESULTS_STREAM, result, maxlen=1000)
        finished = time.time()
        self.set_status(
            state="idle",
            last_finished_at=int(finished),
            last_duration_s=int(finished - now),
            last_trigger=trigger,
            last_version=result["version"],
            last_passed=result["passed"],
            last_reason=result.get("reason", ""),
        )
        log.info("training finished: %s", result)
        return result

    def step(self, now: float | None = None) -> dict | None:
        now = now or time.time()
        cfg = Config.load(self.r)
        drift = self.update_drift(now)
        interval = cfg.retrain_interval_hours * 3600
        last = float(self.status().get("last_finished_at") or self.started)
        self.set_status(next_scheduled_at=int(last + interval) if cfg.auto_retrain else "")
        if self.status().get("state") != "training":
            self.set_status(state="idle")
        trigger = self.trigger(cfg, drift, now)
        return self.run_training(cfg, trigger) if trigger else None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[trainer] %(levelname)s %(message)s")
    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    trainer = Trainer(redis.Redis.from_url(url), Path(os.environ.get("MODELS_DIR", "/models")), url)
    while True:
        try:
            heartbeat()
            trainer.step()
        except redis.ConnectionError as exc:
            log.warning("redis unavailable (%s)", exc)
        except Exception:
            log.exception("trainer step failed")
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
