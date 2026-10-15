"""Train the Dense-AE (and the IsolationForest baseline), evaluate on a held-out simulated
population, and export a deployable model directory.

    python -m ztna_ml.train --out /models [--seed 1] [--hours 3] [--quick]

Produces <out>/<version>/{model.onnx, meta.json, report.json, report.md}.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import redis

from . import datagen
from .evaluate import evaluate
from .features import FEATURES
from .livecheck import Span, collect_windows, in_spans, score_report
from .model import OnnxAutoencoder
from .models import dense_ae, iforest, lstm_ae


def log(msg: str) -> None:
    print(f"[train] {msg}", flush=True)


def render_markdown(version: str, ae: dict, base: dict, info: dict) -> str:
    """Human-readable report (Ukrainian; it is shown as is in the dashboard)."""
    sequences = "train_sequences" in info
    lines = [
        f"# Модель {version}",
        "",
        f"Навчено на {info.get('train_windows', info.get('train_sequences'))} нормальних "
        f"{'послідовностях' if sequences else 'вікнах'}; перевірено на {ae['normal_windows']} "
        f"нормальних і {ae['attack_windows']} атакуючих вікнах невідомої симульованої популяції.",
        "",
        f"| Метрика | {info.get('kind', 'dense_ae')} | IsolationForest |",
        "|---|---|---|",
    ]
    for k in ["fpr", "precision", "recall", "f1", "roc_auc"]:
        lines.append(f"| {k} | {ae[k]} | {base[k]} |")
    lines += [
        "",
        f"Лише правила: частка хибних спрацювань {ae['rules_fpr']} на нормальних вікнах.",
        "",
        "| Атака | вікон | recall правил | recall AE | recall правила+AE | лише AE |",
        "|---|---|---|---|---|---|",
    ]
    for kind, s in ae["per_kind"].items():
        lines.append(
            f"| {kind} | {s['windows']} | {s['rules_recall']} | {s['model_recall']} | "
            f"{s['combined_recall']} | {s['model_only']} |"
        )
    if live := info.get("live_holdout"):
        lines += [
            "",
            "Відкладений реальний трафік (останні хвилини, у навчанні не використовувались): "
            f"{live['normal_windows']} нормальних вікон, частка хибних спрацювань "
            f"{live['normal_fpr']}; {live['attack_windows']} вікон атакуючих, "
            f"recall {'—' if live['attack_recall'] is None else live['attack_recall']}.",
        ]
    lines += [
        "",
        "| Атака | епізодів | виявлено (правила+AE) | медіана вікон до виявлення |",
        "|---|---|---|---|",
    ]
    for kind, s in ae["episodes"].items():
        lines.append(
            f"| {kind} | {s['episodes']} | {s['combined']} | {s['median_windows_to_detect']} |"
        )
    return "\n".join(lines) + "\n"


def real_baseline(args) -> tuple[datagen.Dataset, datagen.Dataset, list]:
    """Clean normal windows of this network: no rule hits, no known attacker IPs.
    Split by time: older 70% train, newer 30% validation; the last minutes are held out."""
    r = redis.Redis.from_url(args.redis_url)
    now = time.time()
    cut = now - args.holdout_minutes * 60
    base = collect_windows(r, now - args.real_minutes * 60, cut)
    holdout = collect_windows(r, cut, now)
    excluded = set(args.exclude_ip)
    clean = sorted(
        (
            lw.window
            for lw in base
            if not lw.rule_hit
            and lw.window.peer_ip not in excluded
            and not in_spans(lw.window, args.exclude_span)
            and lw.window.count >= 3
        ),
        key=lambda w: w.start,
    )
    if len(clean) < 20:
        raise SystemExit(f"only {len(clean)} clean real windows; let traffic run longer")
    ds = datagen.Dataset(
        np.array([w.vector() for w in clean], dtype=np.float32),
        np.array(["normal"] * len(clean)),
        np.array([w.peer_ip for w in clean]),
        np.array([w.start for w in clean]),
        np.zeros(len(clean), dtype=bool),
        np.array([w.count for w in clean]),
    )
    split_t = clean[int(len(clean) * 0.7)].start
    return ds.subset(ds.starts < split_t), ds.subset(ds.starts >= split_t), holdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--normal-peers", type=int, default=16)
    ap.add_argument("--quick", action="store_true", help="small run for smoke tests")
    ap.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL"),
        help="learn the baseline of this network from its own traffic",
    )
    ap.add_argument(
        "--real-minutes",
        type=float,
        default=0,
        help="minutes of real traffic to learn from (0 = synthetic only)",
    )
    ap.add_argument(
        "--holdout-minutes",
        type=float,
        default=20,
        help="most recent real minutes kept aside for evaluation",
    )
    ap.add_argument(
        "--exclude-ip",
        action="append",
        default=[],
        help="peer IPs that are not normal (attack simulators)",
    )
    ap.add_argument(
        "--exclude-span",
        action="append",
        type=Span.parse,
        default=[],
        help="ip,start,end: a peer's suspect traffic (unix seconds), e.g. around an incident",
    )
    ap.add_argument("--real-weight", type=int, default=5, help="oversampling of real windows")
    ap.add_argument("--model", choices=["dense", "lstm"], default="dense")
    args = ap.parse_args()
    if args.quick:
        args.hours, args.normal_peers = 1.2, 6

    t0 = time.monotonic()
    common = {"normal_peers": args.normal_peers, "hours": args.hours}
    train_ds = datagen.build(args.seed, **common)
    val_ds = datagen.build(args.seed + 1000, **common)
    test_ds = datagen.build(args.seed + 2000, **common)
    log(f"datasets built in {time.monotonic() - t0:.1f}s")

    eligible = lambda ds: ds.counts >= 3  # noqa: E731
    X_train = train_ds.X[(train_ds.labels == "normal") & eligible(train_ds)]
    X_val = val_ds.X[(val_ds.labels == "normal") & eligible(val_ds)]
    log(
        f"train {len(X_train)} / val {len(X_val)} normal windows, "
        f"test {len(test_ds.X)} windows ({(test_ds.labels != 'normal').sum()} attack)"
    )

    holdout = None
    real_train = real_val = None
    if args.real_minutes > 0 and args.redis_url:
        real_train, real_val, holdout = real_baseline(args)
        log(
            f"real baseline: {len(real_train.X)} train / {len(real_val.X)} val windows, "
            f"{len(holdout)} holdout windows"
        )
        X_train = np.vstack([X_train, *[real_train.X] * args.real_weight])
        X_val = np.vstack([X_val, *[real_val.X] * args.real_weight])

    if args.model == "lstm":
        model_report, exporter, info_extra = train_lstm(
            args, train_ds, val_ds, test_ds, real_train, real_val
        )
    else:
        model_report, exporter, info_extra = train_dense(args, X_train, X_val, test_ds, real_val)
    eval_mask = info_extra.pop("eval_mask", None)

    base = iforest.train(X_train, X_val, seed=args.seed)
    base_report = evaluate(test_ds, base.scores(test_ds.X), base.threshold, mask=eval_mask)

    kind = "lstm_ae" if args.model == "lstm" else "dense_ae"
    digest = hashlib.sha1(json.dumps(vars(args) | {"out": ""}, default=str).encode()).hexdigest()[
        :6
    ]
    version = f"{kind.replace('_', '-')}-{datetime.now(UTC):%Y%m%d-%H%M%S}-{digest}"
    out = args.out / version
    out.mkdir(parents=True)
    meta = exporter(out)
    meta |= {
        "version": version,
        "kind": kind,
        "features": FEATURES,
        "created_at": datetime.now(UTC).isoformat(),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))

    # The exported ONNX must reproduce torch's errors, otherwise production != evaluation.
    onnx = OnnxAutoencoder(out)
    drift = info_extra.pop("check_onnx")(onnx)
    assert drift < 1e-3, f"ONNX/torch mismatch {drift}"

    live = (
        score_report(onnx, holdout, set(args.exclude_ip), args.exclude_span)
        if holdout is not None
        else None
    )
    info = {
        "kind": kind,
        "live_holdout": live,
        "args": vars(args) | {"out": str(args.out)},
        "onnx_max_abs_diff": drift,
        **info_extra,
    }
    report = {"version": version, "model": model_report, "isolation_forest": base_report, **info}
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "report.md").write_text(
        render_markdown(version, model_report, base_report, info), encoding="utf-8"
    )
    log(f"exported {out} in {time.monotonic() - t0:.1f}s")
    print((out / "report.md").read_text(encoding="utf-8"))


REAL_QUANTILE = 0.99


def calibrate(threshold: float, real_errors: np.ndarray | None) -> tuple[float, dict]:
    """Synthetic validation dominates the threshold quantile; make sure the threshold also
    holds on this network's own normal traffic."""
    if real_errors is None or len(real_errors) < 20:
        return threshold, {"threshold_synthetic": threshold}
    real = float(np.quantile(real_errors, REAL_QUANTILE))
    log(f"threshold: synthetic {threshold:.4f}, real q{REAL_QUANTILE} {real:.4f}")
    return max(threshold, real), {"threshold_synthetic": threshold, "threshold_real": real}


def train_dense(args, X_train, X_val, test_ds, real_val=None):
    ae = dense_ae.train(X_train, X_val, seed=args.seed)
    real_err = (
        dense_ae.window_errors(ae.model, ae.normalizer, real_val.X)
        if real_val is not None
        else None
    )
    ae.threshold, cal = calibrate(ae.threshold, real_err)
    log(
        f"Dense-AE: {len(ae.history)} epochs, val loss {ae.history[-1][1]:.4f}, "
        f"threshold {ae.threshold:.4f}"
    )
    scores = dense_ae.window_errors(ae.model, ae.normalizer, test_ds.X)
    report = evaluate(test_ds, scores, ae.threshold)

    def exporter(out: Path) -> dict:
        dense_ae.export_onnx(ae.model, len(FEATURES), str(out / "model.onnx"))
        return {
            "mean": ae.normalizer.mean.tolist(),
            "std": ae.normalizer.std.tolist(),
            "threshold": ae.threshold,
        }

    def check(onnx) -> float:
        ratio = np.array([onnx.score([v]) for v in test_ds.X[:200]])
        return float(np.abs(ratio - scores[:200] / ae.threshold).max())

    extra = {
        "train_windows": len(X_train),
        "val_windows": len(X_val),
        "check_onnx": check,
        **cal,
    }
    return report, exporter, extra


def train_lstm(args, train_ds, val_ds, test_ds, real_train, real_val):
    seq_len = lstm_ae.SEQ_LEN

    def normal_seqs(ds):
        S, t = lstm_ae.sequences(ds, seq_len)
        return S[(ds.labels[t] == "normal") & (ds.counts[t] >= 3)]

    S_train, S_val = normal_seqs(train_ds), normal_seqs(val_ds)
    if real_train is not None:
        R_train, R_val = normal_seqs(real_train), normal_seqs(real_val)
        if len(R_train):
            S_train = np.concatenate([S_train, *[R_train] * args.real_weight])
        if len(R_val):
            S_val = np.concatenate([S_val, *[R_val] * args.real_weight])
    log(f"LSTM-AE: {len(S_train)} train / {len(S_val)} val sequences of {seq_len} windows")
    ae = lstm_ae.train(S_train, S_val, seed=args.seed)
    real_err = None
    if real_val is not None and len(R := normal_seqs(real_val)):
        real_err = lstm_ae.sequence_errors(ae.model, ae.normalizer, R)
    ae.threshold, cal = calibrate(ae.threshold, real_err)
    log(
        f"LSTM-AE: {len(ae.history)} epochs, val loss {ae.history[-1][1]:.4f}, "
        f"threshold {ae.threshold:.4f}"
    )

    S_test, targets = lstm_ae.sequences(test_ds, seq_len)
    seq_scores = lstm_ae.sequence_errors(ae.model, ae.normalizer, S_test)
    scores = np.full(len(test_ds.X), -1.0)
    scores[targets] = seq_scores
    mask = np.zeros(len(test_ds.X), dtype=bool)
    mask[targets] = True
    report = evaluate(test_ds, scores, ae.threshold, mask=mask)

    def exporter(out: Path) -> dict:
        lstm_ae.export_onnx(ae.model, seq_len, len(FEATURES), str(out / "model.onnx"))
        return {
            "mean": ae.normalizer.mean.tolist(),
            "std": ae.normalizer.std.tolist(),
            "threshold": ae.threshold,
            "seq_len": seq_len,
        }

    def check(onnx) -> float:
        ratio = np.array([onnx.score(list(S_test[i])) for i in range(50)])
        return float(np.abs(ratio - seq_scores[:50] / ae.threshold).max())

    extra = {
        "train_sequences": len(S_train),
        "val_sequences": len(S_val),
        "seq_len": seq_len,
        "check_onnx": check,
        "eval_mask": mask,
        **cal,
    }
    return report, exporter, extra


if __name__ == "__main__":
    main()
