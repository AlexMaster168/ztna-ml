import json

import numpy as np
import pytest

from ztna_ml import datagen
from ztna_ml.features import FEATURES
from ztna_ml.model import OnnxAutoencoder
from ztna_ml.models import dense_ae, lstm_ae


def test_datagen_produces_labelled_windows():
    ds = datagen.build(
        seed=7,
        normal_peers=3,
        hours=0.3,
        attack_peers=1,
        attack_minutes=3,
        attacks=["bruteforce", "stealth-exfiltration"],
    )
    assert ds.X.shape[1] == len(FEATURES)
    assert {"normal", "bruteforce", "stealth-exfiltration"} <= set(ds.labels)
    # loud brute force is caught by rules, normal browsing is not
    assert ds.rule_hit[ds.labels == "bruteforce"].all()
    assert not ds.rule_hit[ds.labels == "normal"].any()


def test_datagen_is_deterministic():
    a = datagen.build(
        seed=3, normal_peers=2, hours=0.2, attack_peers=1, attack_minutes=2, attacks=["scanner"]
    )
    b = datagen.build(
        seed=3, normal_peers=2, hours=0.2, attack_peers=1, attack_minutes=2, attacks=["scanner"]
    )
    assert np.array_equal(a.X, b.X)


def test_onnx_export_matches_torch(tmp_path):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, len(FEATURES))).astype(np.float32)
    res = dense_ae.train(X[:300], X[300:], epochs=3)
    dense_ae.export_onnx(res.model, len(FEATURES), str(tmp_path / "model.onnx"))
    (tmp_path / "meta.json").write_text(
        json.dumps(
            {
                "version": "t",
                "features": FEATURES,
                "mean": res.normalizer.mean.tolist(),
                "std": res.normalizer.std.tolist(),
                "threshold": res.threshold,
            }
        )
    )
    scorer = OnnxAutoencoder(tmp_path)
    torch_ratio = dense_ae.window_errors(res.model, res.normalizer, X[:20]) / res.threshold
    onnx_ratio = np.array([scorer.score([v]) for v in X[:20]])
    assert np.allclose(torch_ratio, onnx_ratio, atol=1e-4)
    assert len(scorer.explain([X[0]])) == 3


def test_lstm_sequences_and_onnx_roundtrip(tmp_path):
    ds = datagen.build(
        seed=5,
        normal_peers=2,
        hours=0.3,
        attack_peers=1,
        attack_minutes=3,
        attacks=["stealth-exfiltration"],
    )
    S, targets = lstm_ae.sequences(ds, seq_len=4)
    assert S.shape[1:] == (4, len(FEATURES)) and len(S) == len(targets)
    # each sequence ends with its target window and stays within one peer
    assert np.array_equal(S[:, -1], ds.X[targets])
    res = lstm_ae.train(S[: len(S) // 2], S[len(S) // 2 :], epochs=2)
    lstm_ae.export_onnx(res.model, 4, len(FEATURES), str(tmp_path / "model.onnx"))
    (tmp_path / "meta.json").write_text(
        json.dumps(
            {
                "version": "l",
                "kind": "lstm_ae",
                "seq_len": 4,
                "features": FEATURES,
                "mean": res.normalizer.mean.tolist(),
                "std": res.normalizer.std.tolist(),
                "threshold": res.threshold,
            }
        )
    )
    scorer = OnnxAutoencoder(tmp_path)
    assert scorer.history_needed == 4
    torch_ratio = lstm_ae.sequence_errors(res.model, res.normalizer, S[:10]) / res.threshold
    # the scorer receives a longer history and must use only the last seq_len windows
    onnx_ratio = np.array([scorer.score(list(np.vstack([S[i][:1], S[i]]))) for i in range(10)])
    assert np.allclose(torch_ratio, onnx_ratio, atol=1e-4)
    # the worker passes a deque of recent windows
    from collections import deque

    assert scorer.score(deque(S[0], maxlen=32)) == pytest.approx(onnx_ratio[0], abs=1e-5)
