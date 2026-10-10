"""Throughput of the detection path (no Redis): parsing excluded, windows + rules + model.

python -m ztna_ml.bench [--model DIR] [--peers 200] [--minutes 10]
"""

from __future__ import annotations

import argparse
import time

from . import datagen
from .model import load_scorer
from .worker import Detector


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--peers", type=int, default=200)
    ap.add_argument("--minutes", type=float, default=10)
    args = ap.parse_args()

    reqs = []
    for i in range(args.peers):
        prof = "developer" if i % 4 == 0 else "browser"
        reqs += datagen.play(
            prof, f"10.20.{i // 250}.{i % 250 + 1}", 1_800_000_000.0, args.minutes * 60, seed=i
        )
    reqs.sort(key=lambda r: r.ts)
    scorer = load_scorer(args.model) if args.model else None
    det = Detector(scorer)

    windows = 0
    t0 = time.perf_counter()
    model_time = 0.0
    for r in reqs:
        _, _, closed = det.on_request("gw-1", r)
        for w in closed:
            det.peer_rules("gw-1", w)
            m0 = time.perf_counter()
            det.score_window(w, "gw-1")
            model_time += time.perf_counter() - m0
            windows += 1
    elapsed = time.perf_counter() - t0
    simulated = args.minutes * 60
    print(f"model: {scorer.version if scorer else 'none (rules only)'}")
    print(f"{len(reqs)} requests from {args.peers} peers ({simulated:.0f}s of traffic)")
    print(
        f"processed in {elapsed:.2f}s: {len(reqs) / elapsed:,.0f} req/s, "
        f"{simulated / elapsed:,.0f}x real time"
    )
    if windows:
        print(f"{windows} windows scored, {model_time / windows * 1000:.2f} ms per window")


if __name__ == "__main__":
    main()
