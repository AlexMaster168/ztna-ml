import json
from datetime import UTC, datetime

import fakeredis
import pytest

from ztna_ml import worker as wk
from ztna_ml.features import FEATURES, WINDOW_SECONDS
from ztna_ml.shards import shard_of, stream_name
from ztna_ml.worker import Detector

T0 = 1_800_000_000.0


class FakeScorer:
    def __init__(self, version="fake-1", ratio=2.0, history_needed=1):
        self.version = version
        self.ratio = ratio
        self.history_needed = history_needed
        self.seen = []

    def score(self, history):
        self.seen.append(len(history))
        return self.ratio

    def explain(self, history, top=3):
        return FEATURES[:top]


@pytest.fixture
def r():
    return fakeredis.FakeRedis()


def line(t, ip="10.13.0.2", path="/", status=200, size=500):
    return json.dumps(
        {
            "ts": datetime.fromtimestamp(T0 + t, UTC).isoformat(),
            "peer_ip": ip,
            "method": "GET",
            "uri": path,
            "status": status,
            "bytes_sent": size,
            "request_length": 300,
            "rt": 0.01,
            "ua": "Mozilla/5.0",
        }
    )


def ship(r, t, ip="10.13.0.2", gw="gw-1", **kw):
    r.xadd(stream_name(shard_of(gw, ip, 4)), {"gw": gw, "data": line(t, ip, **kw)})


def make_worker(r, name, tmp_path, monkeypatch, shards=4):
    monkeypatch.setattr(wk, "stream_name", stream_name)
    return wk.Worker(r, name, tmp_path, shards=shards)


def test_worker_follows_active_and_shadow_deployment(r, tmp_path, monkeypatch):
    loaded = []
    monkeypatch.setattr(wk, "load_scorer", lambda p: loaded.append(p.name) or FakeScorer(p.name))
    w = make_worker(r, "c1", tmp_path, monkeypatch)
    r.hset("ml:model", mapping={"active": "m-1", "shadow": "m-2"})
    w.refresh_model()
    assert w.models.active.version == "m-1" and w.models.shadow.version == "m-2"
    # promote the challenger: no reload, the loaded scorer moves to the active slot
    r.delete("ml:model")
    r.hset("ml:model", mapping={"active": "m-2"})
    w.refresh_model()
    assert w.models.active.version == "m-2" and w.models.shadow is None
    assert loaded == ["m-1", "m-2"]
    r.hset("ml:model", mapping={"shadow": "../etc"})
    w.refresh_model()
    assert w.versions["shadow"] is None  # path traversal refused
    # deployments written before champion/challenger
    r.delete("ml:model")
    r.hset("ml:model", mapping={"version": "m-3", "mode": "shadow"})
    w.refresh_model()
    assert w.models.active is None and w.models.shadow.version == "m-3"
    assert r.hgetall("ml:worker:c1")[b"shadow"] == b"m-3"


def test_sequence_scorer_waits_for_history():
    det = Detector(FakeScorer(history_needed=3), phases=1)
    results = []
    for k in range(4):
        for i in range(5):
            det.on_request("gw-1", wk.parse_line(line(k * WINDOW_SECONDS + i)))
        w = det.managers["gw-1"][0].open.pop("10.13.0.2")
        results.append(det.score_window(w).ratio)
    assert results == [None, None, 2.0, 2.0]
    assert det.scorer.seen == [3, 4]  # the scorer gets the whole history


def test_two_workers_split_the_shards(r, tmp_path, monkeypatch):
    a = make_worker(r, "a", tmp_path, monkeypatch)
    b = make_worker(r, "b", tmp_path, monkeypatch)
    a.rebalance()
    assert sorted(a.owned) == [0, 1, 2, 3]
    b.rebalance()  # b registers; a sees two workers on its next pass and hands over
    a.rebalance()
    b.rebalance()
    assert len(a.owned) == 2 and len(b.owned) == 2
    assert set(a.owned).isdisjoint(b.owned)


def test_shard_moves_with_state_and_replays_unacked(r, tmp_path, monkeypatch):
    a = make_worker(r, "a", tmp_path, monkeypatch)
    a.rebalance()
    n = shard_of("gw-1", "10.13.0.2", 4)
    for t in range(0, 70, 2):  # two full windows and change
        ship(r, t)
    a.step(block_ms=1)
    a.checkpoint(n)
    vol_before = a.owned[n].detector.volume["gw-1"].export_peer("10.13.0.2")
    assert vol_before["windows"] >= 2
    assert r.xpending(stream_name(n), wk.GROUP)["pending"] == 0
    for t in range(70, 80, 2):  # read but never checkpointed: a crashes
        ship(r, t)
    a.step(block_ms=1)
    assert r.xpending(stream_name(n), wk.GROUP)["pending"] == 5

    r.delete(wk.lease_key(n))  # a's lease expired
    r.zrem(wk.WORKERS_KEY, "a")
    b = make_worker(r, "b", tmp_path, monkeypatch)
    b.rebalance()
    assert n in b.owned
    det = b.owned[n].detector
    assert det.volume["gw-1"].export_peer("10.13.0.2") == vol_before
    # the five entries a never acknowledged were replayed into the open window
    open_w = det.managers["gw-1"][0].open["10.13.0.2"]
    assert open_w.count == 5 + sum(1 for t in range(60, 70, 2))
    b.checkpoint(n)
    assert r.xpending(stream_name(n), wk.GROUP)["pending"] == 0

    # a comes back and tries to checkpoint the shard it no longer owns
    a.checkpoint(n)
    assert n not in a.owned


def test_detections_and_stats_are_written(r, tmp_path, monkeypatch):
    ratios = {"m-1": 2.0, "m-2": 0.5}
    monkeypatch.setattr(wk, "load_scorer", lambda p: FakeScorer(p.name, ratios[p.name]))
    r.hset("ml:model", mapping={"active": "m-1", "shadow": "m-2"})
    w = make_worker(r, "a", tmp_path, monkeypatch)
    w.rebalance()
    for t in range(0, 40, 2):
        ship(r, t)
    w.step(block_ms=1)
    events = r.xrange(wk.EVENTS_STREAM)
    assert any(e[1][b"kind"] == b"traffic_anomaly" for e in events)
    stats = r.hgetall("ml:stats:m-1")
    assert int(stats[b"windows"]) >= 1 and int(stats[b"disagreements"]) >= 1
    assert int(r.hgetall("ml:stats:m-2")[b"windows"]) >= 1


def test_model_detection_reports_the_oldest_window_it_used():
    det = Detector(FakeScorer(history_needed=3), phases=1)
    found = None
    for k in range(4):
        for i in range(5):
            det.on_request("gw-1", wk.parse_line(line(k * WINDOW_SECONDS + i)))
        w = det.managers["gw-1"][0].open.pop("10.13.0.2")
        s = det.score_window(w)
        found = s.detection or found
    # the last decision used windows 1..3
    assert found.details["since"] == int(T0 + WINDOW_SECONDS)
    snap = json.loads(json.dumps(det.export_peer("gw-1", "10.13.0.2")))
    other = Detector(FakeScorer(history_needed=3), phases=1)
    other.import_peer("gw-1", "10.13.0.2", snap)
    assert list(other.starts[("gw-1", "10.13.0.2", 0)]) == list(
        det.starts[("gw-1", "10.13.0.2", 0)]
    )


def test_unknown_window_starts_claim_nothing():
    # checkpoints written before start times were kept
    det = Detector(FakeScorer(history_needed=3), phases=1)
    snap = {"history": [[[0.0] * len(FEATURES)] * 3]}
    det.import_peer("gw-1", "10.13.0.2", snap)
    for i in range(5):
        det.on_request("gw-1", wk.parse_line(line(i)))
    w = det.managers["gw-1"][0].open.pop("10.13.0.2")
    s = det.score_window(w)
    assert s.detection is not None and "since" not in s.detection.details
