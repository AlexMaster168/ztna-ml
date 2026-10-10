"""Detection metrics for anomaly scores on a labelled window dataset."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from sklearn.metrics import roc_auc_score

from .datagen import Dataset
from .worker import MIN_REQUESTS_FOR_MODEL


def evaluate(
    ds: Dataset, scores: np.ndarray, threshold: float, mask: np.ndarray | None = None
) -> dict:
    """Window- and episode-level metrics. `scores` >= threshold means flagged.

    Windows with too few requests are never flagged by the model (as in the worker).
    An episode is one attacker peer's attack segment; it counts as detected if any of its
    windows is flagged, with time-to-detect measured in windows.
    """
    eligible = ds.counts >= MIN_REQUESTS_FOR_MODEL
    if mask is not None:  # e.g. windows that have enough history for a sequence model
        eligible &= mask
    flagged = (scores >= threshold) & eligible
    in_scope = mask if mask is not None else np.ones(len(ds.labels), dtype=bool)
    is_attack = (ds.labels != "normal") & in_scope
    normal = (ds.labels == "normal") & in_scope

    tp = int((flagged & is_attack).sum())
    fp = int((flagged & normal).sum())
    fn = int((~flagged & is_attack).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0

    combined = flagged | ds.rule_hit
    per_kind: dict[str, dict] = {}
    for kind in sorted(set(ds.labels[is_attack])):
        m = ds.labels == kind
        per_kind[kind] = {
            "windows": int(m.sum()),
            "model_recall": round(float(flagged[m].mean()), 4),
            "rules_recall": round(float(ds.rule_hit[m].mean()), 4),
            "combined_recall": round(float(combined[m].mean()), 4),
            "model_only": int((flagged & ~ds.rule_hit & m).sum()),
        }

    episodes: dict[tuple[str, str], list[tuple[float, bool, bool]]] = defaultdict(list)
    for i in np.where(is_attack)[0]:
        episodes[(ds.labels[i], ds.peers[i])].append(
            (ds.starts[i], bool(flagged[i]), bool(ds.rule_hit[i]))
        )
    ep_stats: dict[str, dict] = defaultdict(
        lambda: {"episodes": 0, "model": 0, "combined": 0, "ttd_windows": []}
    )
    for (kind, _peer), wins in episodes.items():
        wins.sort()
        s = ep_stats[kind]
        s["episodes"] += 1
        s["model"] += any(f for _, f, _ in wins)
        hits = [i for i, (_, f, r) in enumerate(wins) if f or r]
        if hits:
            s["combined"] += 1
            s["ttd_windows"].append(hits[0] + 1)
    for s in ep_stats.values():
        ttd = s.pop("ttd_windows")
        s["median_windows_to_detect"] = float(np.median(ttd)) if ttd else None

    auc = roc_auc_score(is_attack[eligible], scores[eligible]) if is_attack.any() else None
    return {
        "threshold": threshold,
        "normal_windows": int(normal.sum()),
        "attack_windows": int(is_attack.sum()),
        "fpr": round(fp / max(int(normal.sum()), 1), 5),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if tp else 0.0,
        "roc_auc": None if auc is None else round(float(auc), 4),
        "rules_fpr": round(float(ds.rule_hit[normal].mean()), 5),
        "per_kind": per_kind,
        "episodes": dict(ep_stats),
    }
