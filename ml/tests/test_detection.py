import json

import numpy as np
import pytest

from ztna_ml.events import Request, parse_line
from ztna_ml.features import FEATURES, GRACE_SECONDS, WINDOW_SECONDS, WindowManager
from ztna_ml.rules import RuleConfig
from ztna_ml.worker import Detector

BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0"
T0 = 1_800_000_000.0  # aligned to a 30s boundary


def req(t=0.0, ip="10.13.0.2", path="/", status=200, method="GET", ua=BROWSER, size=500):
    return Request(T0 + t, ip, method, path, status, size, 300, 0.01, ua)


def test_parse_line_valid_and_malformed():
    raw = json.dumps(
        {
            "ts": "2026-10-10T17:16:31+00:00",
            "peer_ip": "10.13.0.2",
            "method": "GET",
            "uri": "/pages/wiki?x=1",
            "status": 200,
            "bytes_sent": 404,
            "request_length": 73,
            "rt": 0.004,
            "ua": "curl/7.88.1",
            "host": "10.13.0.1",
        }
    )
    r = parse_line(raw)
    assert r is not None and r.path == "/pages/wiki" and r.status == 200
    assert parse_line(b"not json") is None
    assert parse_line(json.dumps({"ts": "x"})) is None


def test_tumbling_windows_per_peer_and_close_on_watermark():
    m = WindowManager()
    m.add(req(1))
    m.add(req(2, ip="10.13.0.3"))
    w, closed = m.add(req(WINDOW_SECONDS + 1))  # next window for .2
    assert [c.peer_ip for c in closed] == ["10.13.0.2"]
    assert closed[0].count == 1 and w.count == 1
    # .3 closes once the watermark passes its end + grace
    assert [c.peer_ip for c in m.advance(T0 + WINDOW_SECONDS + GRACE_SECONDS)] == ["10.13.0.3"]


def test_late_event_folds_into_current_window():
    m = WindowManager()
    m.add(req(WINDOW_SECONDS + 1))
    w, closed = m.add(req(WINDOW_SECONDS - 1))  # slightly late, previous window
    assert w.count == 2 and not closed


def test_novel_ua_only_counts_unseen_agents():
    m = WindowManager()
    m.add(req(1))
    w, _ = m.add(req(WINDOW_SECONDS + 1))  # closes the first window, UA becomes known
    m.add(req(WINDOW_SECONDS + 2, ua="sqlmap/1.8"))
    assert w.n_novel_ua == 1  # only sqlmap is new


def test_feature_vector_shape_and_values():
    m = WindowManager()
    for i in range(4):
        m.add(req(i, path=f"/p{i}", status=404))
    w = m.open["10.13.0.2"]
    v = w.vector()
    assert v.shape == (len(FEATURES),) and v.dtype == np.float32
    f = dict(zip(FEATURES, v, strict=True))
    assert f["ratio_404"] == 1.0 and f["uniq_path_ratio"] == 1.0
    assert f["log_req_count"] == pytest.approx(np.log1p(4))


def kinds(detector, requests):
    out = []
    for r in requests:
        _, ds, _ = detector.on_request("gw-1", r)
        out += [d.kind for d in ds]
    return out


def test_brute_force_fires_once_per_window():
    d = Detector(None, RuleConfig(brute_force_failures=8))
    reqs = [req(i * 0.5, path="/login", method="POST", status=401) for i in range(20)]
    assert kinds(d, reqs) == ["brute_force"]


def test_failed_logins_below_threshold_are_fine():
    d = Detector(None)
    assert kinds(d, [req(i, path="/login", method="POST", status=401) for i in range(3)]) == []


def test_scanner_ua_and_path_enum():
    d = Detector(None)
    reqs = [req(i * 0.2, path=f"/admin{i}.php", status=404, ua="Nikto/2.5.0") for i in range(20)]
    assert sorted(kinds(d, reqs)) == ["path_enumeration", "scanner_user_agent"]


def test_curl_is_not_a_scanner():
    d = Detector(None)
    assert kinds(d, [req(i, ua="curl/8.5.0") for i in range(5)]) == []


def test_exfiltration_by_volume():
    d = Detector(None, RuleConfig(exfil_bytes=10_000_000))
    reqs = [req(i, path="/files/5000", size=5_000_000) for i in range(3)]
    assert kinds(d, reqs) == ["data_exfiltration"]


class FakeScorer:
    version = "fake-1"

    def __init__(self, ratio, history_needed=1):
        self.ratio = ratio
        self.history_needed = history_needed
        self.seen = []

    def score(self, history):
        self.seen.append(len(history))
        return self.ratio

    def explain(self, history, top=3):
        return FEATURES[:top]


def test_model_detection_on_window_close():
    m_hi, m_lo = Detector(FakeScorer(2.0)), Detector(FakeScorer(0.5))
    for det in (m_hi, m_lo):
        for i in range(5):
            det.on_request("gw-1", req(i))
    w = m_hi.managers["gw-1"].open["10.13.0.2"]
    ratio, d = m_hi.score_window(w)
    assert ratio == 2.0 and d.kind == "traffic_anomaly" and d.penalty == 22.0
    assert m_lo.score_window(m_lo.managers["gw-1"].open["10.13.0.2"]) == (0.5, None)


def test_model_skips_sparse_windows():
    det = Detector(FakeScorer(5.0))
    det.on_request("gw-1", req(0))
    assert det.score_window(det.managers["gw-1"].open["10.13.0.2"]) == (None, None)


class FakeRedis:
    def __init__(self, cfg):
        self.cfg = cfg
        self.status = {}

    def hgetall(self, key):
        return {k.encode(): v.encode() for k, v in self.cfg.items()}

    def hset(self, key, mapping):
        self.status[key] = mapping

    def expire(self, key, ttl):
        pass


def test_worker_follows_model_deployment(tmp_path, monkeypatch):
    from ztna_ml import worker as wk

    loaded = []
    monkeypatch.setattr(wk, "load_scorer", lambda p: loaded.append(p) or FakeScorer(2.0))
    r = FakeRedis({"version": "dense-ae-1", "mode": "shadow"})
    w = wk.Worker(r, Detector(None), "c1", tmp_path)
    w.refresh_model()
    assert w.detector.scorer is not None and w.shadow and loaded == [tmp_path / "dense-ae-1"]
    r.cfg["mode"] = "active"
    w.refresh_model()
    assert not w.shadow and len(loaded) == 1  # mode change does not reload
    r.cfg = {"version": "../etc", "mode": "active"}
    w.refresh_model()
    assert w.model_version == "dense-ae-1"  # path traversal refused
    r.cfg = {}
    w.refresh_model()
    assert w.detector.scorer is None and w.shadow
    assert r.status["ml:worker:c1"]["version"] == ""


def test_sequence_scorer_waits_for_history():
    det = Detector(FakeScorer(2.0, history_needed=3))
    results = []
    for k in range(4):
        for i in range(5):
            det.on_request("gw-1", req(k * WINDOW_SECONDS + i))
        w = det.managers["gw-1"].open.pop("10.13.0.2")
        results.append(det.score_window(w)[0])
    assert results == [None, None, 2.0, 2.0]
    assert det.scorer.seen == [3, 4]  # the scorer gets the whole history


def test_drift_measures_change_against_own_baseline():
    m = WindowManager()
    closed = []
    for k in range(6):  # steady light browsing
        for i in range(4):
            closed += m.add(req(k * WINDOW_SECONDS + i, size=2_000))[1]
    for i in range(4):  # then heavy downloads
        closed += m.add(req(6 * WINDOW_SECONDS + i, size=5_000_000))[1]
    closed += m.flush()
    first, steady, heavy = closed[0], closed[4], closed[-1]
    assert first.drift() == (0.0, 0.0)  # no history yet
    assert abs(steady.drift()[0]) < 0.01
    assert heavy.drift()[0] > 5  # short-term mean far above this peer's normal
    f = dict(zip(FEATURES, heavy.vector(), strict=True))
    assert f["bytes_drift"] == pytest.approx(heavy.drift()[0])


def test_sustained_exfiltration_relative_to_peer_baseline():
    from ztna_ml.volume import HORIZON_WINDOWS, VolumeConfig, VolumeTracker

    t = VolumeTracker(VolumeConfig(min_bytes=50_000_000))
    mb = 1_000_000
    # a light user: ~1 MB per window for 30 windows -> never fires
    assert not any(t.observe("light", 1 * mb) for _ in range(30))
    # then 20 MB per window: fires within the 10-minute horizon
    hits = [t.observe("light", 20 * mb) for _ in range(HORIZON_WINDOWS)]
    first = next(i for i, h in enumerate(hits) if h)
    assert hits[first].kind == "sustained_exfiltration" and first < 15
    assert sum(1 for h in hits if h) == 1  # cooldown

    # a heavy user whose normal is 20 MB per window is not flagged for the same rate
    assert not any(t.observe("heavy", 20 * mb) for _ in range(60))


def test_sustained_exfiltration_baseline_resists_poisoning():
    from ztna_ml.volume import VolumeConfig, VolumeTracker

    t = VolumeTracker(VolumeConfig(min_bytes=10_000_000))
    for _ in range(30):
        t.observe("p", 1_000_000)
    for _ in range(60):  # a long exfil cannot drag the baseline up quickly
        t.observe("p", 50_000_000)
    assert t._peers["p"].baseline < 5_000_000


def test_sustained_exfiltration_cold_start_learns_heavy_user():
    from ztna_ml.volume import VolumeConfig, VolumeTracker

    t = VolumeTracker(VolumeConfig(min_bytes=50_000_000))
    t.observe("dev", 2_000)  # tiny first window
    # then a developer's normal ~20 MB/window must not look like exfiltration
    assert not any(t.observe("dev", 20_000_000) for _ in range(120))
