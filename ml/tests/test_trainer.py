import json
import sys

import fakeredis
import pytest

from ztna_ml import trainer as tr
from ztna_ml.features import FEATURES

HOUR = 3600
NOW = 1_800_000_000.0


@pytest.fixture
def r():
    return fakeredis.FakeRedis()


def put_hour(r, version, hour, n, mean, std=1.0, shifted=()):
    mapping = {"n": n}
    for f in FEATURES:
        m = mean + (2.0 if f in shifted else 0.0)
        mapping[f"s:{f}"] = m * n
        mapping[f"q:{f}"] = (std**2 + m**2) * n
    r.hset(f"ml:drift:{version}:{hour}", mapping=mapping)


def test_drift_reference_then_shift(r):
    h = int(NOW // HOUR)
    put_hour(r, "m", h - 5, 300, 0.0)
    assert tr.drift_report(r, "m", NOW)["status"] == "collecting_reference"
    put_hour(r, "m", h - 4, 300, 0.0)
    put_hour(r, "m", h, 200, 0.0)
    rep = tr.drift_report(r, "m", NOW)  # freezes the reference from hours h-5..h
    assert rep["status"] in ("collecting", "ok")
    put_hour(r, "m", h + 1, 400, 0.0, shifted=("bytes_drift", "ratio_4xx"))
    rep = tr.drift_report(r, "m", NOW + HOUR)
    assert rep["status"] == "ok" and rep["score"] > 0
    assert set(list(rep["top_features"])[:2]) == {"bytes_drift", "ratio_4xx"}
    assert rep["top_features"]["bytes_drift"] == pytest.approx(2.0, abs=0.05)


def test_gates():
    good = {"model": {"fpr": 0.004, "recall": 0.8}, "live_holdout": {"normal_fpr": 0.05}}
    assert tr.check_gates(good)[0]
    assert not tr.check_gates({"model": {"fpr": 0.05, "recall": 0.9}})[0]
    assert not tr.check_gates({"model": {"fpr": 0.001, "recall": 0.3}})[0]
    bad_live = {**good, "live_holdout": {"normal_fpr": 0.3}}
    assert "live fpr" in tr.check_gates(bad_live)[1]


def test_triggers(r, tmp_path):
    t = tr.Trainer(r, tmp_path, "redis://x")
    t.started = NOW
    cfg = tr.Config(retrain_interval_hours=24, drift_threshold=0.5)
    assert t.trigger(cfg, None, NOW + 60) is None
    r.rpush(tr.REQUESTS_KEY, "{}")
    assert t.trigger(cfg, None, NOW + 60) == "manual"
    assert t.trigger(cfg, None, NOW + 25 * HOUR) == "schedule"
    drift = {"score": 0.9}
    assert t.trigger(cfg, drift, NOW + HOUR) is None  # cooldown after the last run
    assert t.trigger(cfg, drift, NOW + 3 * HOUR) == "drift"
    cfg.auto_retrain = False
    assert t.trigger(cfg, drift, NOW + 30 * HOUR) is None


FAKE_TRAIN = """
import json, pathlib, sys
out = pathlib.Path(sys.argv[1]) / "lstm-ae-test"
out.mkdir()
(out / "report.json").write_text(json.dumps({"model": {"fpr": 0.003, "recall": 0.9}}))
"""


def test_run_training_reports_result(r, tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    t = tr.Trainer(r, models, "redis://x")
    r.zadd(tr.SUSPICIOUS_KEY, {"10.13.0.5": NOW - 5 * HOUR, "10.13.0.4|1": NOW})
    cmd = t.command(tr.Config(), NOW + 60)
    spans = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--exclude-span"]
    # only the incident inside the training range, and only around it
    assert spans == [f"10.13.0.4,{NOW - tr.SUSPECT_BEFORE:.0f},{NOW + tr.SUSPECT_AFTER:.0f}"]
    monkeypatch.setattr(
        t, "command", lambda cfg, now: [sys.executable, "-c", FAKE_TRAIN, str(models)]
    )
    result = t.run_training(tr.Config(), "manual")
    assert result["passed"] == "1" and result["version"] == "lstm-ae-test"
    entry = r.xrange(tr.RESULTS_STREAM)[-1][1]
    assert entry[b"version"] == b"lstm-ae-test" and entry[b"trigger"] == b"manual"
    status = t.status()
    assert status["state"] == "idle" and status["last_passed"] == "1"

    monkeypatch.setattr(
        t, "command", lambda cfg, now: [sys.executable, "-c", "raise SystemExit('no data')"]
    )
    result = t.run_training(tr.Config(), "schedule")
    assert result["passed"] == "0" and "no data" in result["reason"]
    assert len(r.xrange(tr.RESULTS_STREAM)) == 1  # failures are not offered as candidates
    assert json.loads(json.dumps(t.status()))["last_passed"] == "0"


def test_suspect_spans_cover_only_overlapping_windows():
    from ztna_ml.features import Window
    from ztna_ml.livecheck import Span, in_spans

    span = Span.parse("10.13.0.4,1000,1100")
    assert str(span) == "10.13.0.4,1000,1100"
    assert in_spans(Window("10.13.0.4", 1090.0), [span])  # 1090..1120 overlaps
    assert not in_spans(Window("10.13.0.4", 1100.0), [span])
    assert not in_spans(Window("10.13.0.4", 960.0), [span])  # ends at 990
    assert not in_spans(Window("10.13.0.5", 1050.0), [span])
